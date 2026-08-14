# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest
from frappe.core.api.file import create_new_folder

from cloud_storage.tests.fixtures import OTHER_USER, SHARED_VIEWER


@pytest.fixture(autouse=True)
def force_local_storage(local_storage):
	pass


def test_propfind_hides_unshared_private_file(dav_request, track_files):
	frappe.set_user("Administrator")
	owned = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "webdav_perm_unshared.txt",
			"content": "not shared",
			"is_private": 1,
			"folder": "Home",
		}
	).insert()
	track_files(owned.name)

	frappe.set_user(OTHER_USER)
	resp = dav_request("PROPFIND", "/dav/", headers={"Depth": "1"})
	assert resp.status_code == 207
	assert b"webdav_perm_unshared.txt" not in resp.get_data()


def test_propfind_shows_docshare_file(dav_request, track_files):
	frappe.set_user("Administrator")
	owned = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "webdav_perm_shared.txt",
			"content": "shared via docshare",
			"is_private": 1,
			"folder": "Home",
		}
	).insert()
	track_files(owned.name)
	frappe.share.add("File", owned.name, user=SHARED_VIEWER, read=1)

	frappe.set_user(SHARED_VIEWER)
	resp = dav_request("PROPFIND", "/dav/", headers={"Depth": "1"})
	assert resp.status_code == 207
	assert b"webdav_perm_shared.txt" in resp.get_data()


def test_get_unshared_private_file_returns_404(dav_request, track_files):
	frappe.set_user("Administrator")
	owned = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "webdav_perm_get_unshared.txt",
			"content": "cannot read me",
			"is_private": 1,
			"folder": "Home",
		}
	).insert()
	track_files(owned.name)

	frappe.set_user(OTHER_USER)
	resp = dav_request("GET", "/dav/webdav_perm_get_unshared.txt")
	assert resp.status_code == 404


def test_get_docshare_file_returns_content(dav_request, track_files):
	frappe.set_user("Administrator")
	owned = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "webdav_perm_get_shared.txt",
			"content": "read me please",
			"is_private": 1,
			"folder": "Home",
		}
	).insert()
	track_files(owned.name)
	frappe.share.add("File", owned.name, user=SHARED_VIEWER, read=1)

	frappe.set_user(SHARED_VIEWER)
	resp = dav_request("GET", "/dav/webdav_perm_get_shared.txt")
	assert resp.status_code == 200
	assert resp.get_data() == b"read me please"


def test_put_denied_in_folder_without_write_permission(dav_request, track_files):
	frappe.set_user("Administrator")
	folder = create_new_folder("WebdavPermNoWrite", "Home")
	track_files(folder.name)

	frappe.set_user(OTHER_USER)
	resp = dav_request("PUT", "/dav/WebdavPermNoWrite/blocked.txt", data=b"nope")
	assert resp.status_code == 403

	leftover = frappe.db.get_value(
		"File", {"folder": folder.name, "file_name": "blocked.txt"}, "name"
	)
	assert leftover is None


def test_home_root_is_always_writable_regardless_of_ownership(dav_request, track_files):
	# Documents the current behavior (can_write_folder short-circuits True
	# for "Home") rather than asserting it's the desired end state.
	frappe.set_user(OTHER_USER)
	resp = dav_request("PUT", "/dav/webdav_perm_home_write.txt", data=b"anyone can write Home")
	assert resp.status_code in (200, 201, 204)

	name = frappe.db.get_value(
		"File", {"folder": "Home", "file_name": "webdav_perm_home_write.txt"}, "name"
	)
	track_files(name)
	assert name is not None


def test_delete_allowed_for_owner(dav_request, track_files):
	frappe.set_user("Administrator")
	owned = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "webdav_perm_delete_owner.txt",
			"content": "delete me",
			"is_private": 1,
			"folder": "Home",
		}
	).insert()
	track_files(owned.name)

	resp = dav_request("DELETE", "/dav/webdav_perm_delete_owner.txt")
	assert resp.status_code in (200, 204)
	assert not frappe.db.exists("File", owned.name)


def test_delete_denied_with_read_only_share(dav_request, track_files):
	frappe.set_user("Administrator")
	owned = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "webdav_perm_delete_denied.txt",
			"content": "read only for you",
			"is_private": 1,
			"folder": "Home",
		}
	).insert()
	track_files(owned.name)
	frappe.share.add("File", owned.name, user=SHARED_VIEWER, read=1)

	frappe.set_user(SHARED_VIEWER)
	resp = dav_request("DELETE", "/dav/webdav_perm_delete_denied.txt")
	assert resp.status_code == 403
	assert frappe.db.exists("File", owned.name)


def test_move_denied_without_write_permission_on_source(dav_request, track_files):
	frappe.set_user("Administrator")
	owned = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "webdav_perm_move_src_denied.txt",
			"content": "read only for you",
			"is_private": 1,
			"folder": "Home",
		}
	).insert()
	track_files(owned.name)
	frappe.share.add("File", owned.name, user=SHARED_VIEWER, read=1)

	frappe.set_user(SHARED_VIEWER)
	resp = dav_request(
		"MOVE",
		"/dav/webdav_perm_move_src_denied.txt",
		headers={"Destination": "http://localhost/dav/webdav_perm_move_src_denied_2.txt"},
	)
	assert resp.status_code == 403

	frappe.set_user("Administrator")
	owned.reload()
	assert owned.folder == "Home"
	assert owned.file_name == "webdav_perm_move_src_denied.txt"


def test_move_denied_without_write_permission_on_destination_folder(dav_request, track_files):
	frappe.set_user("Administrator")
	locked_folder = create_new_folder("WebdavPermMoveDestDenied", "Home")
	track_files(locked_folder.name)

	frappe.set_user(OTHER_USER)
	put_resp = dav_request("PUT", "/dav/webdav_perm_move_dest_denied.txt", data=b"mine")
	assert put_resp.status_code in (200, 201, 204)
	owned_name = frappe.db.get_value(
		"File", {"folder": "Home", "file_name": "webdav_perm_move_dest_denied.txt"}, "name"
	)
	track_files(owned_name)

	move_resp = dav_request(
		"MOVE",
		"/dav/webdav_perm_move_dest_denied.txt",
		headers={
			"Destination": "http://localhost/dav/WebdavPermMoveDestDenied/webdav_perm_move_dest_denied.txt"
		},
	)
	assert move_resp.status_code == 403

	frappe.set_user("Administrator")
	moved = frappe.get_doc("File", owned_name)
	assert moved.folder == "Home"


def test_mkcol_denied_without_write_permission(dav_request, track_files):
	frappe.set_user("Administrator")
	parent = create_new_folder("WebdavPermMkcolDenied", "Home")
	track_files(parent.name)

	frappe.set_user(OTHER_USER)
	resp = dav_request("MKCOL", "/dav/WebdavPermMkcolDenied/BlockedSubfolder")
	assert resp.status_code == 403

	leftover = frappe.db.get_value(
		"File",
		{"folder": parent.name, "file_name": "BlockedSubfolder", "is_folder": 1},
		"name",
	)
	assert leftover is None


def test_propfind_on_shared_subfolder_lists_children(dav_request, track_files):
	frappe.set_user("Administrator")
	folder = create_new_folder("WebdavPermSharedSubfolder", "Home")
	track_files(folder.name)
	frappe.share.add("File", folder.name, user=SHARED_VIEWER, read=1)

	child = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "webdav_perm_subfolder_child.txt",
			"content": "in shared subfolder",
			"is_private": 1,
			"folder": folder.name,
		}
	).insert()
	track_files(child.name)

	frappe.set_user(SHARED_VIEWER)
	resp = dav_request("PROPFIND", "/dav/WebdavPermSharedSubfolder/", headers={"Depth": "1"})
	assert resp.status_code == 207
	assert b"webdav_perm_subfolder_child.txt" in resp.get_data()


def test_os_metadata_roundtrip_where_user_can_write(dav_request):
	# Regression for the frappe.local.cache staleness bug in memory.get().
	frappe.set_user(SHARED_VIEWER)

	put_resp = dav_request("PUT", "/dav/.DS_Store", data=b"roundtrip")
	assert put_resp.status_code in (200, 201, 204)

	get_resp = dav_request("GET", "/dav/.DS_Store")
	assert get_resp.status_code == 200
	assert get_resp.get_data() == b"roundtrip"

	delete_resp = dav_request("DELETE", "/dav/.DS_Store")
	assert delete_resp.status_code in (200, 204)

	missing_resp = dav_request("GET", "/dav/.DS_Store")
	assert missing_resp.status_code == 404


def test_os_metadata_put_denied_without_write_permission(dav_request):
	frappe.set_user("Administrator")
	folder = create_new_folder("WebdavOSMetaNoWrite", "Home")
	frappe.share.add("File", folder.name, user=SHARED_VIEWER, read=1)

	frappe.set_user(SHARED_VIEWER)
	resp = dav_request("PUT", "/dav/WebdavOSMetaNoWrite/.DS_Store", data=b"blocked")
	assert resp.status_code == 403

	frappe.set_user("Administrator")
	frappe.delete_doc("File", folder.name, force=True, ignore_permissions=True)


def test_os_metadata_delete_denied_without_write_permission(dav_request):
	frappe.set_user("Administrator")
	folder = create_new_folder("WebdavOSMetaNoDelete", "Home")
	frappe.share.add("File", folder.name, user=SHARED_VIEWER, read=1)
	put_resp = dav_request("PUT", "/dav/WebdavOSMetaNoDelete/.DS_Store", data=b"admin metadata")
	assert put_resp.status_code in (200, 201, 204)

	frappe.set_user(SHARED_VIEWER)
	resp = dav_request("DELETE", "/dav/WebdavOSMetaNoDelete/.DS_Store")
	assert resp.status_code == 403

	frappe.set_user("Administrator")
	get_resp = dav_request("GET", "/dav/WebdavOSMetaNoDelete/.DS_Store")
	assert get_resp.status_code == 200
	assert get_resp.get_data() == b"admin metadata"

	dav_request("DELETE", "/dav/WebdavOSMetaNoDelete/.DS_Store")
	frappe.delete_doc("File", folder.name, force=True, ignore_permissions=True)


def test_os_metadata_hidden_from_directory_listing_without_read_permission(dav_request):
	frappe.set_user("Administrator")
	folder = create_new_folder("WebdavOSMetaNoRead", "Home")
	put_resp = dav_request("PUT", "/dav/WebdavOSMetaNoRead/.DS_Store", data=b"admin only")
	assert put_resp.status_code in (200, 201, 204)

	frappe.set_user(SHARED_VIEWER)
	resp = dav_request("GET", "/dav/WebdavOSMetaNoRead/.DS_Store")
	assert resp.status_code == 404

	frappe.set_user("Administrator")
	dav_request("DELETE", "/dav/WebdavOSMetaNoRead/.DS_Store")
	frappe.delete_doc("File", folder.name, force=True, ignore_permissions=True)


def test_os_metadata_readable_in_folder_inherited_from_shared_grandparent(dav_request):
	# Regression for a folder only reachable via DocShare inheritance, not
	# shared itself.
	frappe.set_user("Administrator")
	parent = create_new_folder("WebdavOSMetaInheritParent", "Home")
	frappe.db.set_value("File", parent.name, "is_private", 1)
	frappe.share.add("File", parent.name, user=SHARED_VIEWER, read=1)
	child = create_new_folder("WebdavOSMetaInheritChild", parent.name)
	frappe.db.set_value("File", child.name, "is_private", 1)

	put_resp = dav_request(
		"PUT",
		"/dav/WebdavOSMetaInheritParent/WebdavOSMetaInheritChild/.DS_Store",
		data=b"inherited read",
	)
	assert put_resp.status_code in (200, 201, 204)

	frappe.set_user(SHARED_VIEWER)
	resp = dav_request("GET", "/dav/WebdavOSMetaInheritParent/WebdavOSMetaInheritChild/.DS_Store")
	assert resp.status_code == 200
	assert resp.get_data() == b"inherited read"

	frappe.set_user("Administrator")
	dav_request("DELETE", "/dav/WebdavOSMetaInheritParent/WebdavOSMetaInheritChild/.DS_Store")
	frappe.delete_doc("File", child.name, force=True, ignore_permissions=True)
	frappe.delete_doc("File", parent.name, force=True, ignore_permissions=True)


def test_os_metadata_move_denied_without_write_permission_on_source(dav_request, track_files):
	frappe.set_user("Administrator")
	folder = create_new_folder("WebdavOSMetaMoveSrcDenied", "Home")
	track_files(folder.name)
	frappe.share.add("File", folder.name, user=SHARED_VIEWER, read=1)
	put_resp = dav_request("PUT", "/dav/WebdavOSMetaMoveSrcDenied/.DS_Store", data=b"stay put")
	assert put_resp.status_code in (200, 201, 204)

	frappe.set_user(SHARED_VIEWER)
	resp = dav_request(
		"MOVE",
		"/dav/WebdavOSMetaMoveSrcDenied/.DS_Store",
		headers={"Destination": "http://localhost/dav/.DS_Store"},
	)
	assert resp.status_code == 403

	frappe.set_user("Administrator")
	still_there = dav_request("GET", "/dav/WebdavOSMetaMoveSrcDenied/.DS_Store")
	assert still_there.status_code == 200
	assert still_there.get_data() == b"stay put"
	not_moved = dav_request("GET", "/dav/.DS_Store")
	assert not_moved.status_code == 404

	dav_request("DELETE", "/dav/WebdavOSMetaMoveSrcDenied/.DS_Store")


def test_os_metadata_move_denied_without_write_permission_on_destination(dav_request, track_files):
	# OTHER_USER, not SHARED_VIEWER: destination-collection resolution goes
	# through can_read_folder before the write check, so a non-SM actor gets
	# 409 (can't see the folder) instead of this test's 403.
	frappe.set_user("Administrator")
	locked_folder = create_new_folder("WebdavOSMetaMoveDestDenied", "Home")
	track_files(locked_folder.name)

	frappe.set_user(OTHER_USER)
	put_resp = dav_request("PUT", "/dav/.DS_Store", data=b"mine")
	assert put_resp.status_code in (200, 201, 204)

	resp = dav_request(
		"MOVE",
		"/dav/.DS_Store",
		headers={"Destination": "http://localhost/dav/WebdavOSMetaMoveDestDenied/.DS_Store"},
	)
	assert resp.status_code == 403

	still_there = dav_request("GET", "/dav/.DS_Store")
	assert still_there.status_code == 200
	assert still_there.get_data() == b"mine"

	frappe.set_user("Administrator")
	not_moved = dav_request("GET", "/dav/WebdavOSMetaMoveDestDenied/.DS_Store")
	assert not_moved.status_code == 404

	dav_request("DELETE", "/dav/.DS_Store")


def test_os_metadata_move_allowed_moves_content_and_removes_source(dav_request, track_files):
	frappe.set_user(SHARED_VIEWER)
	mkcol_resp = dav_request("MKCOL", "/dav/WebdavOSMetaMoveAllowed")
	assert mkcol_resp.status_code in (200, 201)
	folder_name = frappe.db.get_value(
		"File", {"folder": "Home", "file_name": "WebdavOSMetaMoveAllowed", "is_folder": 1}, "name"
	)
	track_files(folder_name)

	put_resp = dav_request("PUT", "/dav/.DS_Store", data=b"relocate me")
	assert put_resp.status_code in (200, 201, 204)

	move_resp = dav_request(
		"MOVE",
		"/dav/.DS_Store",
		headers={"Destination": "http://localhost/dav/WebdavOSMetaMoveAllowed/.DS_Store"},
	)
	assert move_resp.status_code in (201, 204)

	at_destination = dav_request("GET", "/dav/WebdavOSMetaMoveAllowed/.DS_Store")
	assert at_destination.status_code == 200
	assert at_destination.get_data() == b"relocate me"

	at_source = dav_request("GET", "/dav/.DS_Store")
	assert at_source.status_code == 404

	frappe.set_user("Administrator")
	dav_request("DELETE", "/dav/WebdavOSMetaMoveAllowed/.DS_Store")
