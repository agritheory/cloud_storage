# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest

from cloud_storage.tests.fixtures import SHARED_VIEWER


@pytest.fixture(autouse=True)
def force_local_storage(local_storage):
	pass


def test_put_creates_local_file(dav_request, track_files):
	frappe.set_user(SHARED_VIEWER)

	resp = dav_request("PUT", "/dav/webdav_pathing_put.txt", data=b"hello from webdav")
	assert resp.status_code in (200, 201, 204)

	file_name = frappe.db.get_value(
		"File", {"file_name": "webdav_pathing_put.txt", "folder": "Home"}, "name"
	)
	track_files(file_name)
	assert file_name is not None

	doc = frappe.get_doc("File", file_name)
	assert doc.owner == SHARED_VIEWER
	assert doc.is_private == 1


def test_move_updates_folder_and_rejects_dot_segment_destination(dav_request, track_files):
	frappe.set_user(SHARED_VIEWER)

	mkcol_resp = dav_request("MKCOL", "/dav/WebdavMoveDest")
	assert mkcol_resp.status_code in (200, 201)
	folder_name = frappe.db.get_value(
		"File", {"folder": "Home", "file_name": "WebdavMoveDest", "is_folder": 1}, "name"
	)
	track_files(folder_name)

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
	track_files(moved_name)
	assert moved_name is not None

	put_resp2 = dav_request("PUT", "/dav/webdav_pathing_move_2.txt", data=b"another")
	assert put_resp2.status_code in (200, 201, 204)
	leftover = frappe.db.get_value("File", {"file_name": "webdav_pathing_move_2.txt"}, "name")
	track_files(leftover)

	traversal_resp = dav_request(
		"MOVE",
		"/dav/webdav_pathing_move_2.txt",
		headers={"Destination": "http://localhost/dav/.."},
	)
	assert traversal_resp.status_code == 403
