# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

from contextlib import contextmanager
from io import BytesIO
from unittest.mock import patch

import frappe
from botocore.exceptions import EndpointConnectionError
from werkzeug.datastructures import FileMultiDict

from cloud_storage.cloud_storage.local_cache import (
	FIELDS,
	get_connection,
	iso,
	row_to_record,
	update_health,
)
from cloud_storage.cloud_storage.overrides.file import CloudStorageFile


@contextmanager
def override_cache_settings(**overrides):
	original = frappe.conf.cloud_storage_settings
	frappe.conf.cloud_storage_settings = {**original, **overrides}
	try:
		yield
	finally:
		frappe.conf.cloud_storage_settings = original


@contextmanager
def degraded_cloud_storage():
	update_health({"status": "Degraded"})
	try:
		yield
	finally:
		update_health({"status": "Healthy"})


def down_endpoint_error():
	return EndpointConnectionError(endpoint_url="https://down.example")


class FailingConnection:
	def __init__(self, conn, fragments):
		self._conn = conn
		self._fragments = fragments

	def execute(self, sql, *args, **kwargs):
		if any(fragment in sql for fragment in self._fragments):
			raise Exception("boom")
		return self._conn.execute(sql, *args, **kwargs)

	def __getattr__(self, name):
		return getattr(self._conn, name)


@contextmanager
def failing_sql(*fragments):
	real_get_connection = get_connection

	def fake_get_connection():
		return FailingConnection(real_get_connection(), fragments)

	with patch("cloud_storage.cloud_storage.local_cache.get_connection", side_effect=fake_get_connection):
		yield


def create_upload_file(
	content: bytes, is_private: bool = False, file_name: str = "file.png"
) -> CloudStorageFile:
	# unattached: File.has_permission falls back to owner/share checks only
	f = BytesIO(content)

	files = FileMultiDict()
	files.add_file("file", f, file_name)

	frappe.set_user("Administrator")
	frappe.local.request = frappe._dict()
	frappe.local.request.method = "POST"
	frappe.local.request.files = files
	frappe.local.form_dict = frappe._dict()
	frappe.local.form_dict.is_private = is_private
	frappe.local.form_dict.doctype = None
	frappe.local.form_dict.docname = None
	frappe.local.form_dict.fieldname = None
	frappe.local.form_dict.file_url = None
	frappe.local.form_dict.folder = "Home"
	frappe.local.form_dict.file_name = file_name
	frappe.local.form_dict.optimize = False
	file = frappe.call("frappe.handler.upload_file")
	frappe.db.commit()
	return file


def reload_file(file: CloudStorageFile) -> CloudStorageFile:
	# the returned doc still has raw bytes in memory, which get_content() prefers
	return frappe.get_doc("File", file.name)


def create_uncached_cloud_file(content: bytes, file_name: str) -> CloudStorageFile:
	# already in the bucket, no local cache row - for testing warm_on_read
	file = frappe.get_doc(
		{"doctype": "File", "file_name": file_name, "content": content, "is_private": 0}
	)
	file.flags.bypass_local_cache = True
	file.insert(ignore_permissions=True)
	return file


def create_attached_upload(content: bytes, file_name: str, commit: bool = True) -> CloudStorageFile:
	f = BytesIO(content)
	files = FileMultiDict()
	files.add_file("file", f, file_name)

	frappe.set_user("Administrator")
	frappe.local.request = frappe._dict()
	frappe.local.request.method = "POST"
	frappe.local.request.files = files
	frappe.local.form_dict = frappe._dict()
	frappe.local.form_dict.is_private = 0
	frappe.local.form_dict.doctype = "User"
	frappe.local.form_dict.docname = "Administrator"
	frappe.local.form_dict.fieldname = None
	frappe.local.form_dict.file_url = None
	frappe.local.form_dict.folder = "Home"
	frappe.local.form_dict.file_name = file_name
	frappe.local.form_dict.optimize = False
	file = frappe.call("frappe.handler.upload_file")
	if commit:
		frappe.db.commit()
	return file


def create_local_only_file(content: bytes, file_name: str) -> CloudStorageFile:
	old_settings = frappe.conf.cloud_storage_settings
	frappe.conf.cloud_storage_settings = None
	try:
		return create_attached_upload(content, file_name)
	finally:
		frappe.conf.cloud_storage_settings = old_settings


def get_cache(file_name: str):
	cursor = get_connection().execute(f"SELECT {FIELDS} FROM local_file_cache WHERE file = ?", (file_name,))
	return row_to_record(cursor.fetchone())


def set_cache_fields(cache, **fields):
	set_clause = ", ".join(f"{key} = ?" for key in fields)
	values = [iso(value) if hasattr(value, "strftime") else value for value in fields.values()]
	get_connection().execute(f"UPDATE local_file_cache SET {set_clause} WHERE id = ?", (*values, cache.id))
