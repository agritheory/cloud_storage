# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

import base64
import json
import os
import re
import subprocess
import types
import uuid
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import quote, unquote
from urllib.request import urlopen
import tempfile

import frappe
from boto3.exceptions import S3UploadFailedError
from boto3.session import Session
from botocore.config import Config
from botocore.exceptions import ClientError
from frappe import DoesNotExistError, _
from frappe.core.doctype.file.file import File, get_files_path
from frappe.core.doctype.file.utils import decode_file_content, get_content_hash
from frappe.model.rename_doc import rename_doc
from frappe.utils import get_datetime, get_url
from frappe.utils.image import optimize_image, strip_exif_data
from magic import from_buffer
from PIL import UnidentifiedImageError
from werkzeug.datastructures import FileStorage

from cloud_storage.cloud_storage.local_cache import (
	admit_local_cache_record,
	delete_cache_record,
	enforce_local_read_permission,
	enqueue_replication,
	get_cached_content,
	get_live_cache_record,
	is_cloud_storage_degraded,
	is_emergency_ceiling_unrecoverable,
	is_local_cache_enabled,
	read_cache_bytes,
	touch_cache_access,
	tombstone_cache_record,
	warm_local_cache,
	write_local_cache_bytes,
)

FILE_URL = "/api/method/retrieve?key={path}"
URL_PREFIXES = ("http://", "https://", "/api/method/retrieve")


class CloudStorageFile(File):
	@File.is_remote_file.getter
	def is_remote_file(self) -> bool:
		"""
		HASH: bfbebb3d3d9c26eb34ed447112fcd46f1dadff00
		REPO: https://github.com/frappe/frappe
		PATH: frappe/core/doctype/file/file.py
		METHOD: is_remote_file
		"""
		if self.file_url:  # type: ignore
			return self.file_url.startswith(URL_PREFIXES)  # type: ignore
		return not self.content

	def validate_file_path(self, path=None):
		"""
		HASH: 48366c6ecbad44ed24e6d02bdd8f8f189ce58927
		REPO: https://github.com/frappe/frappe
		PATH: frappe/core/doctype/file/file.py
		METHOD: validate_file_path
		"""
		if path is None:
			if self.is_remote_file:
				return
			path = self.get_full_path()
		base_path = os.path.realpath(get_files_path(is_private=self.is_private))
		resolved_path = os.path.realpath(path)
		if os.path.commonpath((base_path, resolved_path)) != base_path:
			frappe.throw(_("The File URL you've entered is incorrect"), title=_("Invalid File URL"))

	def validate(self) -> None:
		"""
		HASH: 69a495579a729909f4df7a45855165eee4a208f4
		REPO: https://github.com/frappe/frappe
		PATH: frappe/core/doctype/file/file.py
		METHOD: validate
		"""
		# guard against recursion: associate_files() can save another File, re-entering validate
		if not self.flags.associating_files:
			self.associate_files()
		if self.flags.cloud_storage or self.flags.ignore_file_validate:
			return
		if not self.is_remote_file:
			self.custom_validate()
		else:
			self.validate_file_url()

	def custom_validate(self):
		if self.is_folder:
			return

		# Ensure correct formatting and type
		self.file_url = unquote(self.file_url) if self.file_url else ""

		self.validate_attachment_references()

		# when dict is passed to get_doc for creation of new_doc, is_new returns None
		# this case is handled inside handle_is_private_changed
		if not self.is_new() and self.has_value_changed("is_private"):
			self.handle_is_private_changed()

		self.validate_file_path()
		self.validate_file_url()

		config = frappe.conf.cloud_storage_settings
		if not config or config.get("use_local"):
			self.validate_file_on_disk()

		self.file_size = frappe.form_dict.file_size or self.file_size

	def after_insert(self) -> File:
		"""
		HASH: bfbebb3d3d9c26eb34ed447112fcd46f1dadff00
		REPO: https://github.com/frappe/frappe
		PATH: frappe/core/doctype/file/file.py
		METHOD: after_insert
		"""
		if self.attached_to_doctype and self.attached_to_name and not self.file_association:  # type: ignore
			if not self.content_hash and "/api/method/retrieve" in self.file_url:  # type: ignore
				associated_doc = frappe.get_value("File", {"file_url": self.file_url}, "name")  # type: ignore
			else:
				associated_doc = frappe.get_value(
					"File",
					{"content_hash": self.content_hash, "name": ["!=", self.name], "is_folder": False},  # type: ignore
				)
			s3_key_from_url = None
			if associated_doc and associated_doc != self.name:
				# Extract s3_key from file_url before clearing it; clearing prevents the
				# delete_file hook from removing the remote object when this duplicate is deleted.
				if "?key=" in (self.file_url or ""):
					s3_key_from_url = self.file_url.split("?key=")[1]
				elif "key=" in (self.file_url or ""):
					s3_key_from_url = self.file_url.split("key=")[1].split("&")[0]
				self.db_set("file_url", "")
				rename_doc(
					self.doctype,
					self.name,
					associated_doc,
					merge=True,
					force=True,
					show_alert=False,
					ignore_permissions=True,
					# validate=False,
				)
			if associated_doc and not self.s3_key:
				# Only write s3_key onto the existing file when we extracted a valid key from
				# this duplicate's URL and the existing file does not already have one.
				existing_s3_key = frappe.db.get_value("File", associated_doc, "s3_key")
				if s3_key_from_url and not existing_s3_key:
					frappe.db.set_value("File", associated_doc, "s3_key", s3_key_from_url)

		elif self.attached_to_doctype and self.attached_to_name and self.file_name:  # type: ignore
			associated_doc = frappe.db.get_value(
				"File",
				{
					"file_name": ["=", self.file_name],
					"content_hash": self.content_hash,
					"name": ["!=", self.name],
					"is_folder": False,
				},
				"name",  # type: ignore
			)
			if associated_doc:
				already_associated = frappe.db.exists(
					"File Association",
					{
						"parent": associated_doc,
						"link_doctype": self.attached_to_doctype,  # type: ignore[has-type]
						"link_name": self.attached_to_name,  # type: ignore[has-type]
					},
				)
				if not already_associated:
					frappe.get_doc(
						{
							"doctype": "File Association",
							"parent": associated_doc,
							"parenttype": "File",
							"parentfield": "file_association",
							**add_child_file_association(
								self.attached_to_doctype,  # type: ignore
								self.attached_to_name,  # type: ignore
							),
						}
					).insert(ignore_permissions=True)

				already_versioned = frappe.db.exists(
					"File Version",
					{"parent": associated_doc, "version": self.content_hash},
				)
				if not already_versioned:
					frappe.get_doc(
						{
							"doctype": "File Version",
							"parent": associated_doc,
							"parenttype": "File",
							"parentfield": "versions",
							"version": str(self.content_hash),
							"user": frappe.session.user,
							"timestamp": get_datetime(),
						}
					).insert(ignore_permissions=True)

				frappe.delete_doc("File", self.name, ignore_permissions=True)

		if self.flags.get("pending_local_cache_path") and frappe.db.exists("File", self.name):
			local_path = self.flags.pending_local_cache_path
			frappe.db.after_commit(lambda: admit_and_enqueue_replication(self, local_path))

	def on_trash(self) -> None:
		"""
		HASH: bfbebb3d3d9c26eb34ed447112fcd46f1dadff00
		REPO: https://github.com/frappe/frappe
		PATH: frappe/core/doctype/file/file.py
		METHOD: on_trash
		"""
		user_roles = frappe.get_roles(frappe.session.user)
		if (
			frappe.session.user != "Administrator"
			and "System Manager" not in user_roles
			and (frappe.get_value(self.attached_to_doctype, self.attached_to_name, "docstatus") == 1)  # type: ignore
		):
			frappe.throw(
				_("This file is attached to a submitted document and cannot be deleted"),
				frappe.PermissionError,
			)
		if self.is_home_folder or self.is_attachments_folder:
			frappe.throw(_("Cannot delete Home and Attachments folders"))
		if len(self.file_association) > 0:
			return
		self.validate_empty_folder()
		self._delete_file_on_disk()
		# even though the code is unreachable, we're keeping it here for reference
		if not self.is_folder and len(self.file_association) > 0:
			self.add_comment_in_reference_doc("Attachment Removed", _("Removed {0}").format(self.file_name))

	def associate_files(
		self, attached_to_doctype: str | None = None, attached_to_name: str | None = None
	) -> None:
		attached_to_doctype = attached_to_doctype or self.attached_to_doctype  # type: ignore
		attached_to_name = attached_to_name or self.attached_to_name  # type: ignore

		if not attached_to_doctype:
			return
		if not self.file_url:  # type: ignore
			client = get_cloud_storage_client()
			path = get_file_path(self, client.folder)
			self.file_url = FILE_URL.format(path=path)
		if not self.content_hash and "/api/method/retrieve" in self.file_url:  # type: ignore
			associated_doc = frappe.get_value("File", {"file_url": self.file_url}, "name")  # type: ignore
		else:
			associated_doc = frappe.get_value(
				"File",
				{"content_hash": self.content_hash, "name": ["!=", self.name], "is_folder": False},  # type: ignore
			)
		if associated_doc and associated_doc != self.name:
			existing_file = frappe.get_doc("File", associated_doc)
			existing_file.attached_to_doctype = attached_to_doctype
			existing_file.attached_to_name = attached_to_name
			already_linked = any(
				assoc.link_doctype == attached_to_doctype and assoc.link_name == attached_to_name
				for assoc in existing_file.file_association
			)
			if not already_linked:
				existing_file.append(
					"file_association",
					add_child_file_association(attached_to_doctype, attached_to_name),
				)
			existing_file.flags.associating_files = True
			existing_file.save()
		else:
			if self.file_association:
				already_linked = any(
					assoc.link_doctype == attached_to_doctype and assoc.link_name == attached_to_name
					for assoc in self.file_association
				)
				if not already_linked:
					self.append(
						"file_association",
						add_child_file_association(attached_to_doctype, attached_to_name),
					)
			else:
				self.append(
					"file_association",
					add_child_file_association(attached_to_doctype, attached_to_name),
				)

	def add_file_version(self, version_id):
		self.append(
			"versions",
			{
				"version": str(version_id),
				"user": frappe.session.user,
				"timestamp": get_datetime(),
			},
		)
		if not self.is_new():
			# File already exists in DB (filename-conflict path in write_file).
			# Frappe's save lifecycle won't persist this file's child tables,
			# so we insert the version record directly.
			frappe.get_doc(
				{
					"doctype": "File Version",
					"parent": self.name,
					"parenttype": "File",
					"parentfield": "versions",
					"version": str(version_id),
					"user": frappe.session.user,
					"timestamp": get_datetime(),
				}
			).insert(ignore_permissions=True)

	def remove_file_association(self, dt: str, dn: str) -> None:
		if len(self.file_association) <= 1:
			frappe.db.delete("File Association", {"parent": self.name})
			frappe.db.commit()
			self.delete()
			return
		to_remove = []
		for idx, row in enumerate(self.file_association):
			if row.link_doctype == dt and row.link_name == dn:
				to_remove.append(row)
				if row.link_doctype == self.attached_to_doctype and row.link_name == self.attached_to_name:  # type: ignore
					# calculate the index of the next file association in the list, looping to the start if already at the end
					next_idx = (idx + 1) % len(self.file_association)
					next_file_association = self.file_association[next_idx]
					self.attached_to_doctype = next_file_association.link_doctype
					self.attached_to_name = next_file_association.link_name
		for row in to_remove:
			self.remove(row)
		for idx, association in enumerate(self.file_association, start=1):
			association.idx = idx
		self.save()

	@frappe.whitelist()
	def get_content(self) -> bytes:
		"""
		HASH: 48366c6ecbad44ed24e6d02bdd8f8f189ce58927
		REPO: https://github.com/frappe/frappe
		PATH: frappe/core/doctype/file/file.py
		METHOD: get_content
		"""
		if self.is_folder:
			frappe.throw(_("Cannot get file contents of a Folder"))

		self.validate_file_path()
		if self.get("content"):
			self._content = self.content
			if self.decode:  # type: ignore
				self._content = decode_file_content(self._content)
				self.decode = False
			# self.content = None # TODO: This needs to happen; make it happen somehow
			return self._content

		if self.file_url:
			self.validate_file_url()

		if self.file_url.startswith("/api/method/retrieve"):
			cached_content = get_cached_content(self)
			if cached_content is not None:
				self._content = cached_content
				return self._content
			if is_cloud_storage_degraded():
				frappe.throw(_("Cloud storage is unavailable and this file is not cached locally"))
			client = get_cloud_storage_client()
			file_object = client.get_object(Bucket=client.bucket, Key=self.s3_key)
			self._content = file_object.get("Body").read()
			warm_local_cache(self, self._content)
		elif self.file_url.startswith("http://") or self.file_url.startswith("https://"):
			self._content = urlopen(self.file_url).read()
		else:
			if not self.is_private:
				file_path = frappe.get_site_path("public", "files", self.file_name)
			else:
				file_path = frappe.get_site_path("private", "files", self.file_name)
			self.validate_file_path(file_path)
			with open(file_path, mode="rb") as f:
				self._content = f.read()
				try:
					# for plain text files
					self._content = self._content.decode()
				except UnicodeDecodeError:
					# for .png, .jpg, etc
					pass
		return self._content

	def get_full_path(self):
		"""
		HASH: bfbebb3d3d9c26eb34ed447112fcd46f1dadff00
		REPO: https://github.com/frappe/frappe
		PATH: frappe/core/doctype/file/file.py
		METHOD: get_full_path
		"""
		"""Returns file path from given file name"""

		file_path = self.file_url or self.file_name

		site_url = get_url()
		if "/files/" in file_path and file_path.startswith(site_url):
			file_path = file_path.split(site_url, 1)[1]

		if "/" not in file_path:
			if self.is_private:
				file_path = f"/private/files/{file_path}"
			else:
				file_path = f"/files/{file_path}"

		if file_path.startswith("/private/files/"):
			file_path = get_files_path(*file_path.split("/private/files/", 1)[1].split("/"), is_private=1)

		elif file_path.startswith("/files/"):
			file_path = get_files_path(*file_path.split("/files/", 1)[1].split("/"))

		elif file_path.startswith(URL_PREFIXES):
			pass

		elif not self.file_url:
			frappe.throw(_("There is some problem with the file url: {0}").format(file_path))

		if not is_safe_path(file_path):
			frappe.throw(_("Cannot access file path {0}").format(file_path))

		if os.path.sep in self.file_name:
			frappe.throw(_("File name cannot have {0}").format(os.path.sep))

		return file_path

	@frappe.whitelist()
	def get_pdf_preview(self):
		if self.is_folder:
			frappe.throw(_("Cannot get file contents of a Folder"))

		ext = self.file_name.split(".")[-1].lower()

		if self.file_url.startswith("/api/method/retrieve"):
			file_bytes = get_cached_content(self)
			if file_bytes is None:
				if is_cloud_storage_degraded():
					frappe.throw(_("Cloud storage is unavailable and this file is not cached locally"))
				client = get_cloud_storage_client()
				file_bytes = client.get_object(Bucket=client.bucket, Key=self.s3_key)["Body"].read()
				warm_local_cache(self, file_bytes)

			with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as temp_file:
				temp_file.write(file_bytes)
				temp_file.flush()

				file_path = Path(temp_file.name)

			return convert_to_pdf_base64(file_path)

		else:
			if not self.is_private:
				file_path = Path(frappe.get_site_path("public", "files", self.file_name))
			else:
				file_path = Path(frappe.get_site_path("private", "files", self.file_name))

			return convert_to_pdf_base64(file_path)


def convert_to_pdf_base64(file_path: Path):
	with tempfile.TemporaryDirectory() as tmpdir:
		tmpdir_path = Path(tmpdir)

		subprocess.run(
			[
				"libreoffice",
				"--headless",
				"--convert-to",
				"pdf",
				"--outdir",
				str(tmpdir_path),
				str(file_path),
			],
			check=True,
		)

		pdf_filename = file_path.with_suffix(".pdf").name
		pdf_path = tmpdir_path / pdf_filename

		with open(pdf_path, "rb") as f:
			pdf_bytes = f.read()
			return base64.b64encode(pdf_bytes).decode("utf-8")


def is_safe_path(path: str) -> bool:
	if path.startswith(URL_PREFIXES):
		return True

	basedir = frappe.get_site_path()
	# ref: https://docs.python.org/3/library/os.path.html#os.path.commonpath
	matchpath = os.path.abspath(path)
	basedir = os.path.abspath(basedir)

	return basedir == os.path.commonpath((basedir, matchpath))


@frappe.whitelist()
def get_sharing_link(docname: str, reset: str | bool | None = None) -> str:
	if isinstance(reset, str):
		reset = json.loads(reset)
	doc = frappe.get_doc("File", docname)
	if doc.is_private:
		frappe.has_permission(
			doctype="File", ptype="share", doc=doc, user=frappe.session.user, throw=True
		)
	if reset or not doc.sharing_link:
		doc.db_set("sharing_link", str(uuid.uuid4().int >> 64))
	return f"{get_url()}/api/method/share?key={doc.sharing_link}"


def strip_special_chars(file_name: str) -> str:
	regex = re.compile(r"[^\w\s_.()-]")
	return regex.sub("", file_name)


@frappe.whitelist()
def get_cloud_storage_client():
	validate_config()

	config: dict = frappe.conf.cloud_storage_settings
	session = Session(
		aws_access_key_id=config.get("access_key"),
		aws_secret_access_key=config.get("secret"),
		region_name=config.get("region"),
	)
	client = session.client(
		"s3", endpoint_url=config.get("endpoint_url"), config=Config(signature_version="s3v4")
	)
	client.bucket = config.get("bucket")
	client.folder = config.get("folder", None)
	client.expiration = config.get("expiration", 120)
	client.get_presigned_url = types.MethodType(get_presigned_url, client)
	client.get_sharing_url = types.MethodType(get_sharing_url, client)

	return client


def validate_config() -> None:
	config: dict = frappe.conf.cloud_storage_settings

	if not config:
		frappe.throw(
			msg=_("Please setup cloud storage settings in your site configuration file"),
			title=_("Cloud storage not configured"),
		)

	if not config.get("endpoint_url"):
		frappe.throw(
			msg=_("Please setup endpoint_url in your site configuration file"),
			title=_("Cloud storage endpoint not configured"),
		)

	if not config.get("access_key"):
		frappe.throw(
			msg=_("Please setup access_key in your site configuration file"),
			title=_("Cloud storage access key not configured"),
		)

	if not config.get("secret"):
		frappe.throw(
			msg=_("Please setup secret in your site configuration file"),
			title=_("Cloud storage secret not configured"),
		)

	if not config.get("region"):
		frappe.throw(
			msg=_("Please setup region in your site configuration file"),
			title=_("Cloud storage region not configured"),
		)

	if not config.get("bucket"):
		frappe.throw(
			msg=_("Please setup bucket in your site configuration file"),
			title=_("Cloud storage bucket not configured"),
		)

	if config.get("local_cache_enabled") and config.get("use_local"):
		frappe.throw(
			msg=_("local_cache_enabled and use_local are mutually exclusive in cloud storage settings"),
			title=_("Conflicting cloud storage settings"),
		)


def get_presigned_url(client, key: str):
	file = frappe.get_value("File", {"s3_key": key}, ["name", "is_private"], as_dict=True)
	if not file:
		raise DoesNotExistError(frappe._("The file you are looking for is not available"))
	expiration = client.expiration if file.is_private else None

	if file.is_private:
		file_doc = frappe.get_doc("File", file.name)
		frappe.has_permission(
			doctype="File", ptype="read", doc=file_doc, user=frappe.session.user, throw=True
		)

	return client.generate_presigned_url(
		ClientMethod="get_object",
		Params={"Bucket": client.bucket, "Key": key},
		ExpiresIn=expiration,
	)


def get_sharing_url(client, key: str) -> str:
	file = frappe.get_value("File", {"sharing_link": key}, ["name", "s3_key"], as_dict=True)
	if not file:
		raise DoesNotExistError(frappe._("The file you are looking for is not available"))

	return client.generate_presigned_url(
		ClientMethod="get_object", Params={"Bucket": client.bucket, "Key": file.s3_key}
	)


def upload_file(file: File) -> File:
	client = get_cloud_storage_client()
	path = get_file_path(file, client.folder)
	file.db_set("file_url", FILE_URL.format(path=path))
	content_type = file.content_type or from_buffer(file.content, mime=True)
	version_id = None
	try:
		response = client.put_object(
			Body=file.content, Bucket=client.bucket, Key=path, ContentType=content_type
		)
		version_id = response.get("VersionId") or file.content_hash
		file.associate_files(file.attached_to_doctype, file.attached_to_name)
	except S3UploadFailedError:
		frappe.throw(_("File Upload Failed. Please try again."))
	except Exception as e:
		frappe.log_error("File Upload Error", e)
	if version_id:
		file.add_file_version(version_id)
	file.db_set("s3_key", path)
	if not file.is_new() and file.content_hash:
		file.db_set("content_hash", file.content_hash)
	return file


def get_file_path(file: File, folder: str | None = None) -> str:
	custom_storage_path_generator = frappe.get_hooks("cloud_storage_path_generator")

	if custom_storage_path_generator and len(custom_storage_path_generator) > 0:
		try:
			generator_fn = frappe.get_attr(custom_storage_path_generator[0])
			return generator_fn(file, folder)
		except Exception as e:
			frappe.log_error(f"Custom path generator failed: {str(e)}", "Cloud Storage Path Error")

	config = frappe.conf.get("cloud_storage_settings", {})
	if config.get("use_legacy_paths", True):
		return _legacy_get_file_path(file, folder)

	if folder:
		return f"{folder}/{file.file_name}"

	return file.file_name


def _legacy_get_file_path(file: File, folder: str | None = None) -> str:
	parent_doctype = file.attached_to_doctype or "No Doctype"

	attached_to_name = ""
	if file.attached_to_name:
		attached_to_name = file.attached_to_name.replace("#", "%23")

	fragments = [
		folder,
		parent_doctype,
		attached_to_name,
		file.file_name.replace("#", "%23"),
	]

	valid_fragments: list[str] = list(filter(None, fragments))
	path = "/".join(valid_fragments)
	return path


def get_file_content_hash(content, content_type):
	try:
		stripped_content = strip_exif_data(content, content_type)
		return get_content_hash(stripped_content)
	except UnidentifiedImageError:
		return get_content_hash(content)


@frappe.whitelist()
def write_file(file: File, remove_spaces_in_file_name: bool = True) -> File:
	if not frappe.conf.cloud_storage_settings or frappe.conf.cloud_storage_settings.get(
		"use_local", False
	):
		file.save_file_on_filesystem()
		return file

	if file.attached_to_doctype == "Data Import":
		file.save_file_on_filesystem()
		return file

	# if a hash-conflict is found, update the existing document with a new file association
	existing_file_hashes = frappe.get_all(
		"File",
		filters={"name": ["!=", file.name], "content_hash": file.content_hash},
		pluck="name",
	)

	if existing_file_hashes:
		file_doc: File = frappe.get_doc("File", existing_file_hashes[0])
		file_doc.associate_files(file.attached_to_doctype, file.attached_to_name)
		file_doc.save()
		return file_doc

	# if a filename-conflict is found, update the existing document with a new version instead
	existing_file_names = frappe.get_all(
		"File", filters={"name": ["!=", file.name], "file_name": file.file_name}, pluck="name"
	)

	if existing_file_names:
		file_doc = frappe.get_doc("File", existing_file_names[0])
		file_doc.update(
			{
				"content": file.content,
				"content_hash": file.content_hash,
				"content_type": file.content_type,
			}
		)
		file_doc.associate_files(file.attached_to_doctype, file.attached_to_name)
		file_doc.flags.bypass_local_cache = file.flags.bypass_local_cache
		file = file_doc

	if remove_spaces_in_file_name:
		file.file_name = file.file_name.replace(" ", "_")

	file.file_name = strip_special_chars(file.file_name)
	file.flags.cloud_storage = True

	if file.flags.bypass_local_cache or not is_local_cache_enabled():
		return upload_file(file)

	return cache_file_locally(file)


def admit_and_enqueue_replication(file: File, local_path: str) -> None:
	try:
		previous_local_path = admit_local_cache_record(file, local_path)
	except Exception:
		frappe.log_error()
		raise
	if previous_local_path and os.path.exists(previous_local_path):
		os.remove(previous_local_path)
	enqueue_replication(file.name)


def cache_file_locally(file: File) -> File:
	"""Write bytes to the local cache and enqueue replication instead of uploading
	synchronously. New files don't have a name yet at this point (before_insert
	runs before autoname), so cache-row admission is deferred to after_insert()."""
	if is_emergency_ceiling_unrecoverable(len(file.content)):
		frappe.throw(_("Local cache emergency ceiling reached and cannot be recovered by eviction."))

	validate_config()
	folder = frappe.conf.cloud_storage_settings.get("folder")
	path = get_file_path(file, folder)
	file.db_set("file_url", FILE_URL.format(path=path))
	file.db_set("s3_key", path)
	if not file.is_new() and file.content_hash:
		file.db_set("content_hash", file.content_hash)

	local_path = write_local_cache_bytes(file)

	if file.name:
		frappe.db.after_commit(lambda: admit_and_enqueue_replication(file, local_path))
	else:
		file.flags.pending_local_cache_path = local_path

	return file


@frappe.whitelist()
def delete_file(file: File, **kwargs) -> File:
	if not frappe.conf.cloud_storage_settings or frappe.conf.cloud_storage_settings.get(
		"use_local", False
	):
		file.delete_file_from_filesystem()
		return file

	if file.is_folder:
		return file

	if is_local_cache_enabled() and is_cloud_storage_degraded():
		tombstone_cache_record(file)
		return file

	if file.file_url and "?key=" in file.file_url:
		key = file.file_url.split("?key=")[1]
		if key:
			client = get_cloud_storage_client()
			try:
				client.delete_object(Bucket=client.bucket, Key=key)
			except ClientError:
				frappe.throw(_("Access denied: Could not delete file"))
			except Exception as e:
				if is_local_cache_enabled():
					tombstone_cache_record(file)
					return file
				print(f"EXCEPTION: {e}")
				frappe.log_error(str(e), "Cloud Storage Error: Could not delete file")

	delete_cache_record(file.name)

	return file


@frappe.whitelist()
def validate_file_content(*args, **kwargs):
	matched_files = []
	files = frappe.request.files

	if "file" in files:
		file: FileStorage = files["file"]
		content_type = guess_type(file.filename)[0]

		# validate filename
		file_name = file.filename
		existing_files_by_name = frappe.get_all(
			"File", filters={"file_name": file_name}, pluck="file_name"
		)

		# validate content hash
		file.stream.seek(0)
		content = file.stream.read()
		content_hash = get_file_content_hash(content, content_type)

		existing_files_by_hash = frappe.get_all(
			"File", filters={"content_hash": content_hash}, pluck="file_name"
		)

		# if no files are found by name or hash, and if the file is an image, match against optimized content
		if not existing_files_by_hash and content_type.startswith("image/"):
			optimized_content = optimize_image(content, content_type)
			optimized_content_hash = get_file_content_hash(optimized_content, content_type)
			existing_files_by_hash = frappe.get_all(
				"File", filters={"content_hash": optimized_content_hash}, pluck="file_name"
			)

		# build a list of matched files
		matched_files = list(set(existing_files_by_name + existing_files_by_hash))

	return {
		"filename_exists": len(existing_files_by_name) > 0,
		"content_exists": len(existing_files_by_hash) > 0,
		"matched_files": matched_files,
	}


def serve_cached_response(key: str) -> bool:
	"""Serve `key` from the local cache into frappe.local.response. True on a hit."""
	if not is_local_cache_enabled():
		return False
	cache = get_live_cache_record({"s3_key": key})
	if not cache:
		return False
	content = read_cache_bytes(cache)
	if content is None:
		return False

	file_doc = frappe.get_doc("File", cache.file) if cache.file else None
	if file_doc:
		enforce_local_read_permission(file_doc)
	touch_cache_access(cache.name)

	frappe.local.response["type"] = "download"
	frappe.local.response["filecontent"] = content
	frappe.local.response["filename"] = file_doc.file_name if file_doc else key.rsplit("/", 1)[-1]
	frappe.local.response["display_content_as"] = "inline"
	frappe.local.response["content_type"] = from_buffer(content, mime=True)
	return True


@frappe.whitelist(allow_guest=True)
def retrieve(key: str) -> None:
	if not key:
		frappe.local.response["body"] = "Key not found"
		return

	if serve_cached_response(key):
		return

	if is_cloud_storage_degraded():
		frappe.local.response["http_status_code"] = 503
		frappe.local.response[
			"body"
		] = "Cloud storage is unavailable and this file is not cached locally"
		return

	client = get_cloud_storage_client()
	signed_url = client.get_presigned_url(key)
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = signed_url


@frappe.whitelist(allow_guest=True)
def share(key: str) -> None:
	if key:
		client = get_cloud_storage_client()
		signed_url = client.get_sharing_url(key)
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = signed_url

	frappe.local.response["body"] = "Key not found"


@frappe.whitelist(methods=["DELETE", "POST"])
def remove_attach():
	"""
	HASH: 354843a7a42249f2bd1a96706a9ae70dedc610ff
	REPO: https://github.com/frappe/frappe
	PATH: frappe/desk/form/utils.py
	METHOD: remove_attach
	"""
	fid = frappe.form_dict.get("fid")
	dt = frappe.form_dict.get("dt")
	dn = frappe.form_dict.get("dn")
	if not all([fid, dt, dn]):
		return
	doc = frappe.get_doc("File", fid)
	doc.remove_file_association(dt, dn)


def add_child_file_association(attached_to_doctype, attached_to_name):
	return {
		"link_doctype": attached_to_doctype,
		"link_name": attached_to_name,
		"user": frappe.session.user,
		"timestamp": get_datetime(),
	}
