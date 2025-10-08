import frappe


def execute():
	from frappe.installer import update_site_config

	cloud_storage_settings = frappe.conf.get("cloud_storage_settings") or {}
	cloud_storage_settings["use_legacy_paths"] = 1
	update_site_config("cloud_storage_settings", cloud_storage_settings)
