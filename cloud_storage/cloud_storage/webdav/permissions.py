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


def resolve_folder(frappe_folder: str) -> str | None:
	"""Doc name of the File folder at this Frappe folder path, or None if it doesn't exist."""
	parent = folder_parent(frappe_folder)
	display = folder_display_name(frappe_folder)
	return frappe.db.get_value(
		"File",
		{"folder": parent, "file_name": display, "is_folder": 1},
		"name",
	)


def can_write_folder(frappe_folder: str) -> bool:
	"""Check write permission on a Frappe folder path, e.g. 'Home/Docs'."""
	if frappe_folder == "Home":
		return True
	folder_name = resolve_folder(frappe_folder)
	if not folder_name:
		return False
	return can(folder_name, "write")


def can_read_folder(frappe_folder: str) -> bool:
	"""Check read permission on a Frappe folder path, e.g. 'Home/Docs'."""
	# get_list, not has_permission: folder-inherited DocShare grants only
	# apply via file_permission_query_conditions, not the single-doc check.
	if frappe_folder == "Home":
		return True
	parent = folder_parent(frappe_folder)
	display = folder_display_name(frappe_folder)
	match = frappe.get_list(
		"File",
		filters={"folder": parent, "file_name": display, "is_folder": 1},
		pluck="name",
		limit_page_length=1,
	)
	return bool(match)
