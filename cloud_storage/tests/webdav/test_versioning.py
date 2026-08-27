# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest

from cloud_storage.tests.fixtures import SHARED_VIEWER
from cloud_storage.tests.webdav.helpers import (
	dav_move,
	file_name_at,
	make_file,
	make_folder,
	put_file,
)


pytestmark = pytest.mark.usefixtures("force_local_storage")


def test_put_overwrite_preserves_doc_identity_and_updates_content(dav_request, track_files):
	frappe.set_user(SHARED_VIEWER)

	name = put_file(dav_request, track_files, "webdav_version_overwrite.txt", b"first version")
	hash1 = frappe.get_doc("File", name).content_hash

	put2 = dav_request("PUT", "/dav/webdav_version_overwrite.txt", data=b"second, different version")
	assert put2.status_code in (200, 201, 204)

	same_name = file_name_at("webdav_version_overwrite.txt")
	assert same_name == name
	assert (
		frappe.db.count("File", {"file_name": "webdav_version_overwrite.txt", "folder": "Home"}) == 1
	)

	doc = frappe.get_doc("File", name)
	assert doc.get_content() == "second, different version"
	assert doc.content_hash != hash1


def test_move_onto_existing_destination_preserves_destination_identity_and_version(
	dav_request, track_files
):
	frappe.set_user(SHARED_VIEWER)

	src_name = put_file(dav_request, track_files, "webdav_version_src.txt", b"source content")
	dest_name = put_file(dav_request, track_files, "webdav_version_dest.txt", b"dest content")
	versions_before = len(frappe.get_doc("File", dest_name).versions)

	move_resp = dav_move(dav_request, "/dav/webdav_version_src.txt", "webdav_version_dest.txt")
	assert move_resp.status_code in (201, 204)

	assert not frappe.db.exists("File", src_name)

	dest_doc = frappe.get_doc("File", dest_name)
	assert dest_doc.get_content() == "source content"
	assert len(dest_doc.versions) > versions_before


def test_move_atomic_save_reuses_displaced_sibling_temp_file(dav_request, track_files):
	frappe.set_user(SHARED_VIEWER)

	# A leftover temp file from an earlier, incomplete atomic save — same
	# naming pattern a client's ".sb-*" scratch folder would leave behind.
	leftover = make_file("notes.txt.sb-oldhex", "stale temp content")
	track_files(leftover.name)

	scratch_folder = make_folder(dav_request, track_files, "notes.txt.sb-newhex")

	scratch_file = put_file(
		dav_request,
		track_files,
		"notes.txt.sb-newhex/notes.txt",
		b"brand new content",
		folder=scratch_folder,
	)

	move_resp = dav_move(dav_request, "/dav/notes.txt.sb-newhex/notes.txt", "notes.txt")
	assert move_resp.status_code in (201, 204)

	assert frappe.db.exists("File", leftover.name)
	final_doc = frappe.get_doc("File", leftover.name)
	assert final_doc.folder == "Home"
	assert final_doc.file_name == "notes.txt"
	assert final_doc.get_content() == "brand new content"

	assert not frappe.db.exists("File", scratch_file)
