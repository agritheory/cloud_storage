# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt
# Frappe File permission helpers used by the WebDAV provider.

import frappe


def folder_display_name(frappe_folder: str) -> str:
	return frappe_folder.rsplit("/", 1)[-1]


def folder_parent(frappe_folder: str) -> str:
	if "/" not in frappe_folder:
		return ""
	return frappe_folder.rsplit("/", 1)[0]


def can(target, ptype: str) -> bool:
	"""Per-doc permission check. target must be a doc name or doc object."""
	return frappe.has_permission("File", doc=target, ptype=ptype, user=frappe.session.user)


def can_create() -> bool:
	"""Doctype-level create check (no specific doc yet)."""
	return frappe.has_permission("File", ptype="create", user=frappe.session.user)


def can_write_folder(frappe_folder: str) -> bool:
	"""Check write permission on a Frappe folder path, e.g. 'Home/Docs'."""
	if frappe_folder == "Home":
		return True
	parent = folder_parent(frappe_folder)
	display = folder_display_name(frappe_folder)
	folder_name = frappe.db.get_value(
		"File",
		{"folder": parent, "file_name": display, "is_folder": 1},
		"name",
	)
	if not folder_name:
		return False
	return can(folder_name, "write")
