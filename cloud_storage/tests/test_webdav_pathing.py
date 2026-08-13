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
	assert doc.get_content() == "hello from webdav"


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
	original_name = frappe.db.get_value(
		"File", {"file_name": "webdav_pathing_move.txt", "folder": "Home"}, "name"
	)
	track_files(original_name)

	move_resp = dav_request(
		"MOVE",
		"/dav/webdav_pathing_move.txt",
		headers={"Destination": "http://localhost/dav/WebdavMoveDest/webdav_pathing_move.txt"},
	)
	assert move_resp.status_code in (201, 204)

	# Renaming onto a fresh destination updates the same doc in place.
	moved_name = frappe.db.get_value(
		"File",
		{"file_name": "webdav_pathing_move.txt", "folder": "Home/WebdavMoveDest"},
		"name",
	)
	assert moved_name == original_name
	moved_doc = frappe.get_doc("File", moved_name)
	assert moved_doc.get_content() == "move me"

	stale_get = dav_request("GET", "/dav/webdav_pathing_move.txt")
	assert stale_get.status_code == 404

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

	untouched = frappe.get_doc("File", leftover)
	assert untouched.folder == "Home"
	assert untouched.file_name == "webdav_pathing_move_2.txt"


def test_move_rejects_url_encoded_dot_segment_destination(dav_request, track_files):
	frappe.set_user(SHARED_VIEWER)

	put_resp = dav_request("PUT", "/dav/webdav_pathing_encoded.txt", data=b"encoded traversal")
	assert put_resp.status_code in (200, 201, 204)
	name = frappe.db.get_value(
		"File", {"file_name": "webdav_pathing_encoded.txt", "folder": "Home"}, "name"
	)
	track_files(name)

	resp = dav_request(
		"MOVE",
		"/dav/webdav_pathing_encoded.txt",
		headers={"Destination": "http://localhost/dav/%2e%2e"},
	)
	assert resp.status_code == 403

	untouched = frappe.get_doc("File", name)
	assert untouched.folder == "Home"
	assert untouched.file_name == "webdav_pathing_encoded.txt"


def test_dot_segment_in_request_path_itself_is_rejected(dav_request):
	frappe.set_user(SHARED_VIEWER)

	resp = dav_request("PROPFIND", "/dav/..", headers={"Depth": "1"})
	assert resp.status_code == 403


@pytest.mark.parametrize(
	"malicious_destination",
	[
		"http://localhost/dav/%2e%2e%2fsecret",
		"http://localhost/dav/%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd",
		"http://localhost/dav/Home/foo%5cbar",
	],
)
def test_move_rejects_encoded_separator_within_a_single_segment(
	dav_request, track_files, malicious_destination
):
	# A raw "/" only splits on the literal character — an encoded separator
	# (%2f) survives inside what looks like one path segment until it's
	# unquoted, so ".." can hide there even past a plain "." / ".." guard.
	frappe.set_user(SHARED_VIEWER)

	put_resp = dav_request("PUT", "/dav/webdav_pathing_embedded_sep.txt", data=b"embedded separator")
	assert put_resp.status_code in (200, 201, 204)
	name = frappe.db.get_value(
		"File", {"file_name": "webdav_pathing_embedded_sep.txt", "folder": "Home"}, "name"
	)
	track_files(name)

	resp = dav_request(
		"MOVE",
		"/dav/webdav_pathing_embedded_sep.txt",
		headers={"Destination": malicious_destination},
	)
	assert resp.status_code == 403

	untouched = frappe.get_doc("File", name)
	assert untouched.folder == "Home"
	assert untouched.file_name == "webdav_pathing_embedded_sep.txt"
