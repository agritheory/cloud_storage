# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

__version__ = "15.7.2"


import frappe
import frappe.desk.form.load
from frappe.core.doctype.file import file as file_module
from frappe.query_builder import DocType

import cloud_storage.permissions  # noqa: F401 — must load before silencing stock File list hook


def empty_file_permission_query_conditions(user=None, doctype=None):
	return None


file_module.get_permission_query_conditions = empty_file_permission_query_conditions


@frappe.whitelist()
def patched_get_attachments(dt, dn):
	if "cloud_storage" not in frappe.get_installed_apps():
		return frappe.get_all(
			"File",
			fields=["name", "file_name", "file_url", "is_private"],
			filters={"attached_to_name": dn, "attached_to_doctype": dt},
		)

	File = DocType("File")
	FileAssociation = DocType("File Association")
	return (
		frappe.qb.from_(FileAssociation)
		.inner_join(File)
		.on(File.name == FileAssociation.parent)
		.select(File.name, File.file_name, File.file_url, File.is_private)
		.where(FileAssociation.link_doctype == dt)
		.where(FileAssociation.link_name == dn)
	).run(as_dict=True)


frappe.desk.form.load.get_attachments = patched_get_attachments
