import json
import re
import types
import uuid

import frappe
from boto3.exceptions import S3UploadFailedError
from boto3.session import Session
from botocore.exceptions import ClientError
from frappe import DoesNotExistError, _
from frappe.core.doctype.file.file import URL_PREFIXES, File
from frappe.permissions import has_user_permission
from frappe.utils import get_url
from magic import from_buffer

FILE_URL = "/api/method/retrieve?key={path}"


class CustomFile(File):
	def has_permission(self, ptype: str | None = None, user: str | None = None) -> bool:
		has_access = False
		user = frappe.session.user if not user else user
		# check if public
		if self.owner == user:
			has_access = True
		elif self.attached_to_doctype and self.attached_to_name:
			reference_doc = frappe.get_doc(self.attached_to_doctype, self.attached_to_name)
			has_access = reference_doc.has_permission()
			if not has_access:
				has_access = has_user_permission(self, user)
		# elif True:
		# Check "shared with"  including parent 'folder' to allow access
		# ...
		else:
			has_access = bool(frappe.has_permission("File", "read", user=user))

		return has_access

	def on_trash(self) -> None:
		user_roles = frappe.get_roles(frappe.session.user)
		if (
			frappe.session.user != "Administrator"
			and "System Manager" not in user_roles
			and (frappe.db.get_value(self.attached_to_doctype, self.attached_to_name, "docstatus") == 1)
		):
			frappe.throw(
				_("This file is attached to a submitted document and cannot be deleted"),
				frappe.PermissionError,
			)

		super().on_trash()

	def validate(self) -> None:
		if self.flags.cloud_storage or self.flags.ignore_file_validate:
			return
		super().validate()

	@property
	def is_remote_file(self) -> bool:
		if self.s3_key:
			return True
		if self.file_url:
			return self.file_url.startswith(URL_PREFIXES)
		return not self.content


@frappe.whitelist()
def get_sharing_link(docname: str, reset: str | bool | None = None) -> str:
	if isinstance(reset, str):
		reset = json.loads(reset)
	doc = frappe.get_doc("File", docname)
	if doc.is_private:
		frappe.has_permission(
			doctype="File", ptype="share", doc=doc.name, user=frappe.session.user, throw=True
		)
	if reset or not doc.sharing_link:
		doc.db_set("sharing_link", str(uuid.uuid4().int >> 64))
	return f"{get_url()}/api/method/share?key={doc.sharing_link}"


def strip_special_chars(file_name: str) -> str:
	regex = re.compile("[^0-9a-zA-Z._-]")
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

	client = session.client("s3", endpoint_url=config.get("endpoint_url"))
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
		frappe.has_permission(
			doctype="File", ptype="read", doc=file.name, user=frappe.session.user, throw=True
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
	file.file_url = FILE_URL.format(path=path)
	content_type = file.content_type or from_buffer(file.content, mime=True)
	try:
		client.put_object(Body=file.content, Bucket=client.bucket, Key=path, ContentType=content_type)
	except S3UploadFailedError:
		frappe.throw(_("File Upload Failed. Please try again."))
	except Exception as e:
		frappe.log_error("File Upload Error", e)
	file.s3_key = path
	return file


def get_file_path(file: File, folder: str | None = None) -> str:
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


@frappe.whitelist()
def write_file(file: File) -> File:
	if not frappe.conf.cloud_storage_settings or frappe.conf.cloud_storage_settings.get(
		"use_local", False
	):
		file.save_file_on_filesystem()
		return file

	if file.attached_to_doctype == "Data Import":
		file.save_file_on_filesystem()
		return file

	if not file.name:
		file.autoname()

	file.file_name = strip_special_chars(file.file_name.replace(" ", "_"))
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
