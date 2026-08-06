# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest
from werkzeug.test import EnvironBuilder

from cloud_storage.tests.fixtures import SHARED_VIEWER


@pytest.fixture(autouse=True)
def local_storage():
	old = getattr(frappe.conf, "cloud_storage_settings", None)
	frappe.conf.cloud_storage_settings = {"use_local": True}
	yield
	frappe.conf.cloud_storage_settings = old
	frappe.set_user("Administrator")


def dav_request(method: str, path: str, data: bytes = b"", headers: dict | None = None):
	from cloud_storage.cloud_storage.webdav.renderer import invoke_webdav

	builder = EnvironBuilder(method=method, path=path, data=data, headers=headers or {})
	return invoke_webdav(builder.get_request())


def test_put_creates_local_file():
	frappe.set_user(SHARED_VIEWER)

	resp = dav_request("PUT", "/dav/webdav_pathing_put.txt", data=b"hello from webdav")
	assert resp.status_code in (200, 201, 204)

	file_name = frappe.db.get_value(
		"File", {"file_name": "webdav_pathing_put.txt", "folder": "Home"}, "name"
	)
	assert file_name is not None

	doc = frappe.get_doc("File", file_name)
	assert doc.owner == SHARED_VIEWER
	assert doc.is_private == 1

	frappe.set_user("Administrator")
	frappe.delete_doc("File", file_name, force=True, ignore_permissions=True)


def test_move_updates_folder_and_rejects_dot_segment_destination():
	frappe.set_user(SHARED_VIEWER)

	mkcol_resp = dav_request("MKCOL", "/dav/WebdavMoveDest")
	assert mkcol_resp.status_code in (200, 201)

	put_resp = dav_request("PUT", "/dav/webdav_pathing_move.txt", data=b"move me")
	assert put_resp.status_code in (200, 201, 204)

	move_resp = dav_request(
		"MOVE",
		"/dav/webdav_pathing_move.txt",
		headers={"Destination": "http://localhost/dav/WebdavMoveDest/webdav_pathing_move.txt"},
	)
	assert move_resp.status_code in (201, 204)

	moved_name = frappe.db.get_value(
		"File",
		{"file_name": "webdav_pathing_move.txt", "folder": "Home/WebdavMoveDest"},
		"name",
	)
	assert moved_name is not None

	put_resp2 = dav_request("PUT", "/dav/webdav_pathing_move_2.txt", data=b"another")
	assert put_resp2.status_code in (200, 201, 204)

	traversal_resp = dav_request(
		"MOVE",
		"/dav/webdav_pathing_move_2.txt",
		headers={"Destination": "http://localhost/dav/.."},
	)
	assert traversal_resp.status_code == 403

	frappe.set_user("Administrator")
	frappe.delete_doc("File", moved_name, force=True, ignore_permissions=True)
	folder_name = frappe.db.get_value(
		"File", {"folder": "Home", "file_name": "WebdavMoveDest", "is_folder": 1}, "name"
	)
	if folder_name:
		frappe.delete_doc("File", folder_name, force=True, ignore_permissions=True)
	leftover = frappe.db.get_value("File", {"file_name": "webdav_pathing_move_2.txt"}, "name")
	if leftover:
		frappe.delete_doc("File", leftover, force=True, ignore_permissions=True)
