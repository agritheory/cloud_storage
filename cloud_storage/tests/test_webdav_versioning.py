# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest

from cloud_storage.tests.fixtures import SHARED_VIEWER


@pytest.fixture(autouse=True)
def force_local_storage(local_storage):
	pass


def test_put_overwrite_preserves_doc_identity_and_updates_content(dav_request, track_files):
	frappe.set_user(SHARED_VIEWER)

	put1 = dav_request("PUT", "/dav/webdav_version_overwrite.txt", data=b"first version")
	assert put1.status_code in (200, 201, 204)
	name = frappe.db.get_value(
		"File", {"file_name": "webdav_version_overwrite.txt", "folder": "Home"}, "name"
	)
	track_files(name)
	hash1 = frappe.get_doc("File", name).content_hash

	put2 = dav_request("PUT", "/dav/webdav_version_overwrite.txt", data=b"second, different version")
	assert put2.status_code in (200, 201, 204)

	same_name = frappe.db.get_value(
		"File", {"file_name": "webdav_version_overwrite.txt", "folder": "Home"}, "name"
	)
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

	put_src = dav_request("PUT", "/dav/webdav_version_src.txt", data=b"source content")
	assert put_src.status_code in (200, 201, 204)
	src_name = frappe.db.get_value(
		"File", {"file_name": "webdav_version_src.txt", "folder": "Home"}, "name"
	)
	track_files(src_name)

	put_dest = dav_request("PUT", "/dav/webdav_version_dest.txt", data=b"dest content")
	assert put_dest.status_code in (200, 201, 204)
	dest_name = frappe.db.get_value(
		"File", {"file_name": "webdav_version_dest.txt", "folder": "Home"}, "name"
	)
	track_files(dest_name)
	versions_before = len(frappe.get_doc("File", dest_name).versions)

	move_resp = dav_request(
		"MOVE",
		"/dav/webdav_version_src.txt",
		headers={"Destination": "http://localhost/dav/webdav_version_dest.txt"},
	)
	assert move_resp.status_code in (201, 204)

	assert not frappe.db.exists("File", src_name)

	dest_doc = frappe.get_doc("File", dest_name)
	assert dest_doc.get_content() == "source content"
	assert len(dest_doc.versions) > versions_before


def test_move_atomic_save_reuses_displaced_sibling_temp_file(dav_request, track_files):
	frappe.set_user(SHARED_VIEWER)

	# A leftover temp file from an earlier, incomplete atomic save — same
	# naming pattern a client's ".sb-*" scratch folder would leave behind.
	leftover = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "notes.txt.sb-oldhex",
			"content": "stale temp content",
			"is_private": 1,
			"folder": "Home",
		}
	).insert()
	track_files(leftover.name)

	mkcol = dav_request("MKCOL", "/dav/notes.txt.sb-newhex")
	assert mkcol.status_code in (200, 201)
	scratch_folder = frappe.db.get_value(
		"File", {"folder": "Home", "file_name": "notes.txt.sb-newhex", "is_folder": 1}, "name"
	)
	track_files(scratch_folder)

	put_resp = dav_request("PUT", "/dav/notes.txt.sb-newhex/notes.txt", data=b"brand new content")
	assert put_resp.status_code in (200, 201, 204)
	scratch_file = frappe.db.get_value(
		"File", {"folder": scratch_folder, "file_name": "notes.txt"}, "name"
	)
	track_files(scratch_file)

	move_resp = dav_request(
		"MOVE",
		"/dav/notes.txt.sb-newhex/notes.txt",
		headers={"Destination": "http://localhost/dav/notes.txt"},
	)
	assert move_resp.status_code in (201, 204)

	assert frappe.db.exists("File", leftover.name)
	final_doc = frappe.get_doc("File", leftover.name)
	assert final_doc.folder == "Home"
	assert final_doc.file_name == "notes.txt"
	assert final_doc.get_content() == "brand new content"

	assert not frappe.db.exists("File", scratch_file)
