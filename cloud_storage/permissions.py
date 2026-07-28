# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from frappe.core.doctype.file.file import (
	get_permission_query_conditions as core_file_permission_query_conditions,
)
from frappe.model.db_query import cast_name


def file_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if user == "Administrator":
		return ""

	base = core_file_permission_query_conditions(user)
	share = docshare_file_condition(user)
	if not share:
		return base
	if not base:
		return share
	return f"(({base}) OR ({share}))"


def docshare_file_condition(user):
	escaped_user = frappe.db.escape(user)
	file_name = cast_name("`tabFile`.name")
	direct_share = f"""{file_name} IN (
		SELECT `share_name` FROM `tabDocShare`
		WHERE `share_doctype` = 'File'
		AND `read` = 1
		AND (`user` = {escaped_user} OR `everyone` = 1)
	)"""
	folder_inherit = f"""EXISTS (
		SELECT 1 FROM `tabDocShare` ds
		INNER JOIN `tabFile` shared_folder ON shared_folder.name = ds.share_name
		WHERE ds.share_doctype = 'File'
		AND ds.read = 1
		AND (ds.user = {escaped_user} OR ds.everyone = 1)
		AND shared_folder.is_folder = 1
		AND (
			`tabFile`.folder = shared_folder.name
			OR `tabFile`.folder LIKE CONCAT(shared_folder.name, '/', '%')
		)
	)"""
	return f"(({direct_share}) OR ({folder_inherit}))"
