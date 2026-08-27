# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import os
from unittest.mock import patch

import frappe
import pytest


@pytest.fixture
def s3_backend(mocked_s3_client):
	with (
		patch(
			"cloud_storage.cloud_storage.webdav.paths.get_cloud_storage_client",
			return_value=mocked_s3_client,
		),
		patch(
			"cloud_storage.cloud_storage.webdav.provider.get_cloud_storage_client",
			return_value=mocked_s3_client,
		),
		patch(
			"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
			return_value=mocked_s3_client,
		),
	):
		yield mocked_s3_client


@pytest.fixture
def local_storage():
	old = getattr(frappe.conf, "cloud_storage_settings", None)
	frappe.conf.cloud_storage_settings = {"use_local": True}
	yield
	frappe.conf.cloud_storage_settings = old
	frappe.set_user("Administrator")


@pytest.fixture
def dav_request():
	def send_dav_request(method: str, path: str, data: bytes = b"", headers: dict | None = None):
		from werkzeug.test import EnvironBuilder

		from cloud_storage.cloud_storage.webdav.renderer import invoke_webdav

		builder = EnvironBuilder(method=method, path=path, data=data, headers=headers or {})
		return invoke_webdav(builder.get_request())

	return send_dav_request


@pytest.fixture
def dav_before_request():
	"""Drive the real handle_webdav_methods before_request hook, not invoke_webdav directly."""

	def send_before_request(method: str, path: str, data: bytes = b"", headers: dict | None = None):
		from werkzeug.test import EnvironBuilder

		from cloud_storage.cloud_storage.webdav.renderer import WebdavResponse, handle_webdav_methods

		builder = EnvironBuilder(method=method, path=path, data=data, headers=headers or {})
		frappe.local.request = builder.get_request()
		try:
			handle_webdav_methods()
			return None
		except WebdavResponse as raised:
			return raised.get_response()

	return send_before_request


@pytest.fixture
def track_files():
	"""Register File docs for teardown cleanup that runs even on assertion failure."""
	created = []
	disk_paths = set()

	def track(name):
		created.append(name)
		if name and frappe.db.exists("File", name):
			doc = frappe.get_doc("File", name)
			if not doc.is_folder:
				disk_paths.add(doc.get_full_path())
		return name

	yield track

	frappe.set_user("Administrator")
	# reversed: a folder registered before its children would hit FolderNotEmpty
	for name in reversed(created):
		if name and frappe.db.exists("File", name):
			frappe.delete_doc("File", name, force=True, ignore_permissions=True)
	# Some flows (e.g. MOVE onto an existing destination) detach a source
	# doc's file_url before deleting it, so on_trash never unlinks the disk
	# copy — sweep paths captured at registration time as a backstop.
	for path in disk_paths:
		if path and os.path.exists(path):
			os.remove(path)
