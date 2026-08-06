# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest
from frappe.core.api.file import create_new_folder

from cloud_storage.tests.fixtures import OTHER_USER, SHARED_VIEWER


@pytest.fixture(autouse=True)
def force_local_storage(local_storage):
	pass


def cleanup_file(name):
	if name and frappe.db.exists("File", name):
		frappe.delete_doc("File", name, force=True, ignore_permissions=True)


def test_propfind_hides_unshared_private_file(dav_request):
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

	frappe.set_user(OTHER_USER)
	resp = dav_request("PROPFIND", "/dav/", headers={"Depth": "1"})
	assert resp.status_code == 207
	assert b"webdav_perm_unshared.txt" not in resp.get_data()

	frappe.set_user("Administrator")
	cleanup_file(owned.name)


def test_propfind_shows_docshare_file(dav_request):
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
	frappe.share.add("File", owned.name, user=SHARED_VIEWER, read=1)

	frappe.set_user(SHARED_VIEWER)
	resp = dav_request("PROPFIND", "/dav/", headers={"Depth": "1"})
	assert resp.status_code == 207
	assert b"webdav_perm_shared.txt" in resp.get_data()

	frappe.set_user("Administrator")
	cleanup_file(owned.name)


def test_get_unshared_private_file_returns_404(dav_request):
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

	frappe.set_user(OTHER_USER)
	resp = dav_request("GET", "/dav/webdav_perm_get_unshared.txt")
	assert resp.status_code == 404

	frappe.set_user("Administrator")
	cleanup_file(owned.name)


def test_get_docshare_file_returns_content(dav_request):
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
	frappe.share.add("File", owned.name, user=SHARED_VIEWER, read=1)

	frappe.set_user(SHARED_VIEWER)
	resp = dav_request("GET", "/dav/webdav_perm_get_shared.txt")
	assert resp.status_code == 200
	assert resp.get_data() == b"read me please"

	frappe.set_user("Administrator")
	cleanup_file(owned.name)


def test_put_denied_in_folder_without_write_permission(dav_request):
	frappe.set_user("Administrator")
	folder = create_new_folder("WebdavPermNoWrite", "Home")

	frappe.set_user(OTHER_USER)
	resp = dav_request("PUT", "/dav/WebdavPermNoWrite/blocked.txt", data=b"nope")
	assert resp.status_code == 403

	leftover = frappe.db.get_value(
		"File", {"folder": folder.name, "file_name": "blocked.txt"}, "name"
	)
	assert leftover is None

	frappe.set_user("Administrator")
	cleanup_file(folder.name)


def test_home_root_is_always_writable_regardless_of_ownership(dav_request):
	# Documents the current behavior (can_write_folder short-circuits True
	# for "Home") rather than asserting it's the desired end state.
	frappe.set_user(OTHER_USER)
	resp = dav_request("PUT", "/dav/webdav_perm_home_write.txt", data=b"anyone can write Home")
	assert resp.status_code in (200, 201, 204)

	name = frappe.db.get_value(
		"File", {"folder": "Home", "file_name": "webdav_perm_home_write.txt"}, "name"
	)
	assert name is not None

	frappe.set_user("Administrator")
	cleanup_file(name)
