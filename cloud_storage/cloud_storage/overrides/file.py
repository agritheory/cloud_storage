import json
import os
import re
import types
import uuid
from mimetypes import guess_type
from typing import Optional, Union

import frappe
from boto3.exceptions import S3UploadFailedError
from boto3.session import Session
from botocore.config import Config
from botocore.exceptions import ClientError
from frappe import DoesNotExistError, _
from frappe.core.doctype.file.file import File, get_files_path
from frappe.core.doctype.file.utils import decode_file_content, get_content_hash
from frappe.model.rename_doc import rename_doc
from frappe.permissions import has_user_permission
from frappe.utils import get_datetime, get_url
from frappe.utils.image import optimize_image, strip_exif_data
from magic import from_buffer
from PIL import UnidentifiedImageError
from werkzeug.datastructures import FileStorage
from urllib.parse import quote

FILE_URL = "/api/method/retrieve?key={path}"
URL_PREFIXES = ("http://", "https://", "/api/method/retrieve")


class CustomFile(File):
	def has_permission(self, ptype: Optional[str] = None, user: Optional[str] = None) -> bool:
		return has_permission(self, ptype, user)

	def on_trash(self) -> None:
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
		self, attached_to_doctype: Optional[str] = None, attached_to_name: Optional[str] = None
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
			associated_doc = frappe.get_value("File", {"file_url": self.file_url}, "name")
		else:
			associated_doc = frappe.get_value(
				"File",
				{"content_hash": self.content_hash, "name": ["!=", self.name], "is_folder": False},  # type: ignore
			)
		if associated_doc and associated_doc != self.name:
			existing_file = frappe.get_doc("File", associated_doc)
			existing_file.attached_to_doctype = attached_to_doctype
			existing_file.attached_to_name = attached_to_name
			self.content_hash = existing_file.content_hash
			# if a File exists already where this association should be, we continue validating that File at this time
			# the original File will then be removed in the after insert hook
			self = existing_file

		existing_attachment = list(
			filter(
				lambda row: row.link_doctype == attached_to_doctype and row.link_name == attached_to_name,
				self.file_association,
			)
		)
		if not existing_attachment:
			self.append(
				"file_association",
				{
					"link_doctype": attached_to_doctype,
					"link_name": attached_to_name,
					"user": frappe.session.user,
					"timestamp": get_datetime(),
				},
			)
		if associated_doc and associated_doc != self.name:
			self.save()

	def validate(self) -> None:
		self.associate_files()
		if self.flags.cloud_storage or self.flags.ignore_file_validate:
			return
		if not self.is_remote_file:
			super().validate()
		else:
			self.validate_file_url()

	def after_insert(self) -> File:
		if self.attached_to_doctype and self.attached_to_name and not self.file_association:  # type: ignore
			if not self.content_hash and "/api/method/retrieve" in self.file_url:
				associated_doc = frappe.get_value("File", {"file_url": self.file_url}, "name")
			else:
				associated_doc = frappe.get_value(
					"File",
					{"content_hash": self.content_hash, "name": ["!=", self.name], "is_folder": False},
					"name",
				)
			if associated_doc:
				self.db_set(
					"file_url", ""
				)  # this is done to prevent deletion of the remote file with the delete_file hook
				rename_doc(
					self.doctype,
					self.name,
					associated_doc,
					merge=True,
					force=True,
					show_alert=False,
					ignore_permissions=True,
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

	def remove_file_association(self, dt: str, dn: str) -> None:
		if len(self.file_association) <= 1:
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

	@property
	def is_remote_file(self) -> bool:
		if self.file_url:
			return self.file_url.startswith(URL_PREFIXES)
		return not self.content

	def get_content(self) -> bytes:
		if self.is_folder:
			frappe.throw(_("Cannot get file contents of a Folder"))

		if self.get("content"):
			self._content = self.content
			if self.decode:  # type: ignore
				self._content = decode_file_content(self._content)
				self.decode = False
			# self.content = None # TODO: This needs to happen; make it happen somehow
			return self._content

		if self.file_url:
			self.validate_file_url()
		file_path = quote(self.get_full_path())

		if self.is_remote_file:
			client = get_cloud_storage_client()
			file_object = client.get_object(Bucket=client.bucket, Key=self.s3_key)
			self._content = file_object.get("Body").read()
		else:
			# read the file
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


def has_permission(doc, ptype: Optional[str] = None, user: Optional[str] = None) -> bool:
	has_access = False
	user = frappe.session.user if not user else user
	# check if public
	if doc.owner == user:
		has_access = True
	elif doc.attached_to_doctype and doc.attached_to_name:  # type: ignore
		reference_doc = frappe.get_doc(doc.attached_to_doctype, doc.attached_to_name)  # type: ignore
		has_access = reference_doc.has_permission()
		if not has_access:
			has_access = has_user_permission(doc, user)
	# elif True:
	# Check "shared with"  including parent 'folder' to allow access
	# ...
	else:
		has_access = bool(frappe.has_permission(doc.doctype, ptype, user=user))

	return has_access


def is_safe_path(path: str) -> bool:
	if path.startswith(URL_PREFIXES):
		return True

	basedir = frappe.get_site_path()
	# ref: https://docs.python.org/3/library/os.path.html#os.path.commonpath
	matchpath = os.path.abspath(path)
	basedir = os.path.abspath(basedir)

	return basedir == os.path.commonpath((basedir, matchpath))


@frappe.whitelist()
def get_sharing_link(docname: str, reset: Optional[Union[str, bool]] = None) -> str:
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
	try:
		response = client.put_object(
			Body=file.content, Bucket=client.bucket, Key=path, ContentType=content_type
		)
		if response.get("VersionId"):
			file.add_file_version(response.get("VersionId"))
	except S3UploadFailedError:
		frappe.throw(_("File Upload Failed. Please try again."))
	except Exception as e:
		frappe.log_error("File Upload Error", e)
	file.db_set("s3_key", path)
	if not file.name:
		file.save()
	return file


def get_file_path(file: File, folder: Optional[str] = None) -> str:
	parent_doctype = file.attached_to_doctype or "No Doctype"

	fragments = [
		folder,
		parent_doctype,
		file.attached_to_name,
		file.file_name,
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
		"File", filters={"name": ["!=", file.name], "content_hash": file.content_hash}, pluck="name"
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
			{"content": file.content, "content_hash": file.content_hash, "content_type": file.content_type}
		)
		file_doc.associate_files(file.attached_to_doctype, file.attached_to_name)
		file = file_doc

	if remove_spaces_in_file_name:
		file.file_name = file.file_name.replace(" ", "_")

	file.file_name = strip_special_chars(file.file_name)
	file.flags.cloud_storage = True
	return upload_file(file)


@frappe.whitelist()
def delete_file(file: File, **kwargs) -> File:
	if not frappe.conf.cloud_storage_settings or frappe.conf.cloud_storage_settings.get(
		"use_local", False
	):
		file.delete_file_from_filesystem()
		return file

	if file.is_folder:
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
				frappe.log_error(str(e), "Cloud Storage Error: Cloud not delete file")

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


@frappe.whitelist(allow_guest=True)
def retrieve(key: str) -> None:
	if key:
		client = get_cloud_storage_client()
		signed_url = client.get_presigned_url(key)
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = signed_url

	frappe.local.response["body"] = "Key not found"


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
	fid = frappe.form_dict.get("fid")
	dt = frappe.form_dict.get("dt")
	dn = frappe.form_dict.get("dn")
	if not all([fid, dt, dn]):
		return
	doc = frappe.get_doc("File", fid)
	doc.remove_file_association(dt, dn)
