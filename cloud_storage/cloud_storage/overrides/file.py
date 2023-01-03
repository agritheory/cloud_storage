import re
import types

import frappe
from boto3.exceptions import S3UploadFailedError
from boto3.session import Session
from botocore.exceptions import ClientError
from frappe import _
from frappe.core.doctype.file.file import File
from magic import from_buffer


class CustomFile(File):
	def has_permission(self, ptype: str = None, user: str = None) -> bool:
		has_access = False
		user = frappe.session.user if not user else user

		if self.owner == user:
			has_access = True
		elif self.attached_to_doctype and self.attached_to_name:
			if reference_doc := frappe.get_doc(self.attached_to_doctype, self.attached_to_name):
				has_access = reference_doc.has_permission()
			elif frappe.db.exists(
				{
					"doctype": "User Permission",
					"allow": self.attached_to_doctype,
					"for_value": self.attached_to_name,
					"user": user,
				}
			):
				has_access = True
		else:
			has_access = frappe.has_permission("File", "read", user=user)

		return has_access

	def on_trash(self):
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

	def validate(self):
		if self.flags.cloud_storage or self.flags.ignore_file_validate:
			return

		super().validate()


def strip_special_chars(file_name: str):
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
	return client


def validate_config():
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
	return client.generate_presigned_url(
		ClientMethod="get_object", Params={"Bucket": client.bucket, "Key": key}, ExpiresIn=120
	)


def upload_file(file: File):
	client = get_cloud_storage_client()
	parent_doctype = file.attached_to_doctype or "No Doctype"

	fragments = [
		client.folder,
		parent_doctype,
		file.attached_to_name,
		file.file_name,
	]
	valid_fragments = filter(None, fragments)
	path = "/".join(valid_fragments)

	file.file_url = get_file_url(path)
	content_type = file.content_type or from_buffer(file.content, mime=True)

	try:
		client.put_object(Body=file.content, Bucket=client.bucket, Key=path, ContentType=content_type)
	except S3UploadFailedError:
		frappe.throw(_("File Upload Failed. Please try again."))
	except Exception as e:
		frappe.log_error("File Upload Error", e)

	return file


@frappe.whitelist()
def write_file(file: File):
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
def delete_file(file: File, **kwargs):
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


def get_file_url(path: str):
	return f"/api/method/cloud_storage.cloud_storage.overrides.file.retrieve?key={path}"


@frappe.whitelist(allow_guest=True)
def retrieve(key: str):
	if key:
		client = get_cloud_storage_client()
		signed_url = client.get_presigned_url(key)
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = signed_url
		return signed_url

	frappe.local.response["body"] = "Key not found"
