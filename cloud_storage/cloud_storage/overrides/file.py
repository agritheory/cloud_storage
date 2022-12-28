import os
import re
import types
from pathlib import Path
from urllib.parse import unquote

import boto3
import botocore
import frappe
import magic
from frappe.core.doctype.file.file import File
from frappe.utils import cint, get_files_path


class CustomFile(File):
	def has_permission(self, ptype=None, user=None):
		has_access = False
		user = frappe.session.user if not user else user

		if self.owner == user:
			has_access = True
		elif self.attached_to_doctype and self.attached_to_name:
			if frappe.get_doc(self.attached_to_doctype, self.attached_to_name):
				has_access = frappe.get_doc(self.attached_to_doctype, self.attached_to_name).has_permission()
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
		if frappe.db.get_value(self.attached_to_doctype, self.attached_to_name, "docstatus") == 1:
			if ("System Manager" not in frappe.get_roles(frappe.session.user)) and (
				frappe.session.user != "Administrator"
			):
				frappe.throw(
					frappe._("This file is attached to a submitted document and cannot be deleted"),
					frappe.PermissionError,
				)
		super().on_trash()

	def validate_url(self):
		if self.flags.cloud_storage:
			return

		if not self.file_url or self.file_url.startswith(("http://", "https://")):
			if not self.flags.ignore_file_validate:
				self.validate_file()
			return

		# Probably an invalid web URL
		if not self.file_url.startswith(("/files/", "/private/files/")):
			frappe.throw(frappe._("URL must start with http:// or https://"), title=frappe._("Invalid URL"))

		# Ensure correct formatting and type
		self.file_url = unquote(self.file_url)
		self.is_private = cint(self.is_private)

		self.handle_is_private_changed()

		base_path = os.path.realpath(get_files_path(is_private=self.is_private))
		if not os.path.realpath(self.get_full_path()).startswith(base_path):
			frappe.throw(
				frappe._("The File URL you've entered is incorrect"),
				title=frappe._("Invalid File URL"),
			)


def strip_special_chars(file_name):
	regex = re.compile("[^0-9a-zA-Z._-]")
	return regex.sub("", file_name)


@frappe.whitelist()
def get_cloud_storage_client():
	session = boto3.session.Session()
	client = session.client(
		"s3",
		region_name=frappe.conf.cloud_storage_settings.get("region", ""),
		endpoint_url=frappe.conf.cloud_storage_settings.get("endpoint_url", ""),
		aws_access_key_id=frappe.conf.cloud_storage_settings.get("access_key", ""),
		aws_secret_access_key=frappe.conf.cloud_storage_settings.get("secret", ""),
	)
	client.bucket = frappe.conf.cloud_storage_settings.get("bucket", "")
	client.folder = frappe.conf.cloud_storage_settings.get("folder", None)
	client.expiration = frappe.conf.cloud_storage_settings.get("expiration", 120)
	client.get_presigned_url = types.MethodType(get_presigned_url, client)
	return client


def get_presigned_url(self, key):
	return self.generate_presigned_url(
		"get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=120
	)


def upload_file(doc):
	client = get_cloud_storage_client()
	parent_doctype = doc.attached_to_doctype if doc.attached_to_doctype else "No Doctype"
	path = f"{client.folder + '/' if client.folder else ''}{parent_doctype}/{doc.attached_to_name + '/' if doc.attached_to_name else ''}{doc.file_name if doc.file_name else ''}"
	doc.file_url = get_file_url(f"{path}")
	content_type = magic.from_buffer(doc.content, mime=True)

	try:
		client.put_object(Body=doc.content, Bucket=client.bucket, Key=path, ContentType=content_type)
	except boto3.exceptions.S3UploadFailedError:
		frappe.throw(frappe._("File Upload Failed. Please try again."))
	except Exception as e:
		frappe.log_error("File Upload Error", e)

	return doc


@frappe.whitelist()
def write_file(doc):
	if not frappe.conf.cloud_storage_settings or frappe.conf.cloud_storage_settings.get(
		"use_local", False
	):
		doc.save_file_on_filesystem()
		return doc

	if doc.attached_to_doctype in ("Data Import", "Data Import Legacy"):
		doc.save_file_on_filesystem()
		return doc

	if not doc.name:
		doc.autoname()

	doc.file_name = strip_special_chars(doc.file_name.replace(" ", "_"))
	doc.flags.cloud_storage = True
	return upload_file(doc)


@frappe.whitelist()
def delete_file(doc, **kwargs):
	if not frappe.conf.cloud_storage_settings:
		doc.delete_file_from_filesystem()
		return doc

	if frappe.conf.cloud_storage_settings.get("use_local", False):
		doc.delete_file_from_filesystem()
		return doc

	if doc.is_folder:
		return doc

	if doc.file_url and "?key=" in doc.file_url:
		key = doc.file_url.split("?key=")[1]
	else:
		return doc

	if key:
		try:
			client = get_cloud_storage_client()
			client.delete_object(Bucket=client.bucket, Key=key)
		except botocore.exceptions.ClientError:
			frappe.throw(frappe._("Access denied: Could not delete file"))
		except Exception as e:
			frappe.log_error(e, "Cloud Storage Error: Cloud not delete file")

	return doc


def get_file_url(path):
	url = ".".join(Path(__file__).parts[Path(__file__).parts.index("apps") + 2 :])[:-3]
	return f"/api/method/{url}.retrieve?key={path}"


@frappe.whitelist(allow_guest=True)
def retrieve(key):
	if key:
		client = get_cloud_storage_client()
		signed_url = client.get_presigned_url(key)
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = signed_url
		return signed_url

	frappe.local.response["body"] = "Key not found"
