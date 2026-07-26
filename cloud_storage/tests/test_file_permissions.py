# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from frappe.core.api.file import create_new_folder
from frappe.tests.utils import FrappeTestCase

SHARED_USER = "test4@example.com"
OTHER_USER = "test@example.com"


class TestFileDocSharePermissions(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_direct_docshare_lists_private_file(self):
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "docshare_direct_test.txt",
				"content": "shared privately",
				"is_private": 1,
				"folder": "Home",
			}
		).insert()

		frappe.share.add("File", file_doc.name, user=SHARED_USER, read=1)

		frappe.set_user(SHARED_USER)
		visible = frappe.get_list("File", filters={"folder": "Home"}, pluck="name")
		self.assertIn(file_doc.name, visible)

		frappe.set_user(OTHER_USER)
		visible_other = frappe.get_list("File", filters={"folder": "Home"}, pluck="name")
		self.assertNotIn(file_doc.name, visible_other)

	def test_folder_docshare_lists_child_file(self):
		folder = create_new_folder("DocShare Inherit Folder", "Home")
		frappe.share.add("File", folder.name, user=SHARED_USER, read=1)

		child = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "docshare_folder_child.txt",
				"content": "in shared folder",
				"is_private": 1,
				"folder": folder.name,
			}
		).insert()

		frappe.set_user(SHARED_USER)
		home_files = frappe.get_list("File", filters={"folder": "Home"}, pluck="name")
		self.assertIn(folder.name, home_files)

		folder_files = frappe.get_list("File", filters={"folder": folder.name}, pluck="name")
		self.assertIn(child.name, folder_files)

	def test_unshared_private_file_not_leaked(self):
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "docshare_no_leak.txt",
				"content": "not shared",
				"is_private": 1,
				"folder": "Home",
			}
		).insert()

		frappe.set_user(SHARED_USER)
		visible = frappe.get_list("File", filters={"folder": "Home"}, pluck="name")
		self.assertNotIn(file_doc.name, visible)

	def test_shared_user_has_read_permission_on_file(self):
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "docshare_has_permission_test.txt",
				"content": "permission check",
				"is_private": 1,
				"folder": "Home",
			}
		).insert()

		frappe.share.add("File", file_doc.name, user=SHARED_USER, read=1)

		frappe.set_user(SHARED_USER)
		self.assertTrue(frappe.has_permission("File", "read", doc=file_doc.name))
