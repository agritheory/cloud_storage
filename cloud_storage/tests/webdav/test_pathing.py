# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest

from cloud_storage.tests.fixtures import SHARED_VIEWER
from cloud_storage.tests.webdav.helpers import dav_move, file_name_at, make_folder, put_file


pytestmark = pytest.mark.usefixtures("local_storage")


def test_put_creates_local_file(dav_request, track_files):
	frappe.set_user(SHARED_VIEWER)

	name = put_file(dav_request, track_files, "webdav_pathing_put.txt", b"hello from webdav")
	assert name is not None

	doc = frappe.get_doc("File", name)
	assert doc.owner == SHARED_VIEWER
	assert doc.is_private == 1
	assert doc.get_content() == "hello from webdav"


def test_move_updates_folder_and_rejects_dot_segment_destination(dav_request, track_files):
	frappe.set_user(SHARED_VIEWER)

	make_folder(dav_request, track_files, "WebdavMoveDest")
	original_name = put_file(dav_request, track_files, "webdav_pathing_move.txt", b"move me")

	move_resp = dav_move(
		dav_request, "/dav/webdav_pathing_move.txt", "WebdavMoveDest/webdav_pathing_move.txt"
	)
	assert move_resp.status_code in (201, 204)

	# Renaming onto a fresh destination updates the same doc in place.
	moved_name = file_name_at("webdav_pathing_move.txt", "Home/WebdavMoveDest")
	assert moved_name == original_name
	moved_doc = frappe.get_doc("File", moved_name)
	assert moved_doc.get_content() == "move me"

	stale_get = dav_request("GET", "/dav/webdav_pathing_move.txt")
	assert stale_get.status_code == 404

	put_file(dav_request, track_files, "webdav_pathing_move_2.txt", b"another")

	traversal_resp = dav_request(
		"MOVE",
		"/dav/webdav_pathing_move_2.txt",
		headers={"Destination": "http://localhost/dav/.."},
	)
	assert traversal_resp.status_code == 403

	leftover = file_name_at("webdav_pathing_move_2.txt")
	untouched = frappe.get_doc("File", leftover)
	assert untouched.folder == "Home"
	assert untouched.file_name == "webdav_pathing_move_2.txt"


def test_move_rejects_url_encoded_dot_segment_destination(dav_request, track_files):
	frappe.set_user(SHARED_VIEWER)

	name = put_file(dav_request, track_files, "webdav_pathing_encoded.txt", b"encoded traversal")

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

	name = put_file(
		dav_request, track_files, "webdav_pathing_embedded_sep.txt", b"embedded separator"
	)

	resp = dav_request(
		"MOVE",
		"/dav/webdav_pathing_embedded_sep.txt",
		headers={"Destination": malicious_destination},
	)
	assert resp.status_code == 403

	untouched = frappe.get_doc("File", name)
	assert untouched.folder == "Home"
	assert untouched.file_name == "webdav_pathing_embedded_sep.txt"
