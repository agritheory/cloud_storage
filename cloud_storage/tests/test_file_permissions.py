# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from frappe.core.api.file import create_new_folder

from cloud_storage.tests.fixtures import OTHER_USER, SHARED_VIEWER


def test_direct_docshare_lists_private_file():
	frappe.set_user("Administrator")

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "docshare_direct_test.txt",
			"content": "shared privately",
			"is_private": 1,
			"folder": "Home",
		}
	).insert()

	frappe.share.add("File", file_doc.name, user=SHARED_VIEWER, read=1)

	frappe.set_user(SHARED_VIEWER)
	visible = frappe.get_list("File", filters={"folder": "Home"}, pluck="name")
	assert file_doc.name in visible

	frappe.set_user(OTHER_USER)
	visible_other = frappe.get_list("File", filters={"folder": "Home"}, pluck="name")
	assert file_doc.name not in visible_other

	frappe.set_user("Administrator")


def test_folder_docshare_lists_child_file():
	frappe.set_user("Administrator")

	folder = create_new_folder("DocShare Inherit Folder", "Home")
	frappe.share.add("File", folder.name, user=SHARED_VIEWER, read=1)

	child = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "docshare_folder_child.txt",
			"content": "in shared folder",
			"is_private": 1,
			"folder": folder.name,
		}
	).insert()

	frappe.set_user(SHARED_VIEWER)
	home_files = frappe.get_list("File", filters={"folder": "Home"}, pluck="name")
	assert folder.name in home_files

	folder_files = frappe.get_list("File", filters={"folder": folder.name}, pluck="name")
	assert child.name in folder_files

	frappe.set_user("Administrator")


def test_unshared_private_file_not_leaked():
	frappe.set_user("Administrator")

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "docshare_no_leak.txt",
			"content": "not shared",
			"is_private": 1,
			"folder": "Home",
		}
	).insert()

	frappe.set_user(SHARED_VIEWER)
	visible = frappe.get_list("File", filters={"folder": "Home"}, pluck="name")
	assert file_doc.name not in visible

	frappe.set_user("Administrator")


def test_shared_user_has_read_permission_on_file():
	frappe.set_user("Administrator")

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "docshare_has_permission_test.txt",
			"content": "permission check",
			"is_private": 1,
			"folder": "Home",
		}
	).insert()

	frappe.share.add("File", file_doc.name, user=SHARED_VIEWER, read=1)

	frappe.set_user(SHARED_VIEWER)
	assert frappe.has_permission("File", "read", doc=file_doc.name)

	frappe.set_user("Administrator")
