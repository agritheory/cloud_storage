import os
import re
import types
from urllib.parse import unquote

import frappe
from boto3.exceptions import S3UploadFailedError
from boto3.session import Session
from botocore.exceptions import ClientError
from frappe import _
from frappe.core.doctype.file.file import File
from frappe.utils import cint, get_files_path
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

	def validate_url(self):
		if self.flags.cloud_storage:
			return

		if not self.file_url or self.file_url.startswith(("http://", "https://")):
			# TODO: figure out which function this should point to
			# if not self.flags.ignore_file_validate:
			# 	self.validate_file()
			return

		# Probably an invalid web URL
		if not self.file_url.startswith(("/files/", "/private/files/")):
			frappe.throw(_("URL must start with http:// or https://"), title=_("Invalid URL"))

		# Ensure correct formatting and type
		self.file_url = unquote(self.file_url)
		self.is_private = cint(self.is_private)

		self.handle_is_private_changed()

		base_path = os.path.realpath(get_files_path(is_private=self.is_private))
		if not os.path.realpath(self.get_full_path()).startswith(base_path):
			frappe.throw(
				msg=_("The File URL you've entered is incorrect"),
				title=_("Invalid File URL"),
			)


def strip_special_chars(file_name: str):
	regex = re.compile("[^0-9a-zA-Z._-]")
	return regex.sub("", file_name)


@frappe.whitelist()
def get_cloud_storage_client():
	session = Session()
	cloud_storage_conf = frappe.conf.cloud_storage_settings
	if not cloud_storage_conf:
		frappe.throw(
			msg=_("Please setup cloud storage settings in your site configuration file"),
			title=_("Cloud Storage not configured"),
		)

	client = session.client(
		"s3",
		region_name=cloud_storage_conf.get("region", ""),
		endpoint_url=cloud_storage_conf.get("endpoint_url", ""),
		aws_access_key_id=cloud_storage_conf.get("access_key", ""),
		aws_secret_access_key=cloud_storage_conf.get("secret", ""),
	)

	client.bucket = cloud_storage_conf.get("bucket", "")
	client.folder = cloud_storage_conf.get("folder", None)
	client.expiration = cloud_storage_conf.get("expiration", 120)
	client.get_presigned_url = types.MethodType(get_presigned_url, client)
	return client


def get_presigned_url(client, key: str):
	return client.generate_presigned_url(
		"get_object", Params={"Bucket": client.bucket, "Key": key}, ExpiresIn=120
	)


def upload_file(file: File):
	client = get_cloud_storage_client()
	parent_doctype = file.attached_to_doctype if file.attached_to_doctype else "No Doctype"
	path = f"{client.folder}/{parent_doctype}/{file.attached_to_name or ''}/{file.file_name or ''}"
	file.file_url = get_file_url(path)
	content_type = from_buffer(file.content, mime=True)

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
				frappe.log_error(e, "Cloud Storage Error: Cloud not delete file")

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
