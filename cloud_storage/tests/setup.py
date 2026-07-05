# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from frappe.desk.page.setup_wizard.setup_wizard import setup_complete


def before_test():
	"""Initialize test environment with complete setup and fixture data."""
	frappe.clear_cache()
	today = frappe.utils.getdate()
	setup_complete(
		{
			"currency": "USD",
			"full_name": "Administrator",
			"company_name": "Test Company",
			"timezone": "America/New_York",
			"company_abbr": "TC",
			"domains": ["Distribution"],
			"country": "United States",
			"fy_start_date": today.replace(month=1, day=1).isoformat(),
			"fy_end_date": today.replace(month=12, day=31).isoformat(),
			"language": "english",
			"company_tagline": "Test Company",
			"email": "Administrator",
			"password": "admin",
			"chart_of_accounts": "Standard with Numbers",
			"bank_account": "Primary Checking",
		}
	)
	for modu in frappe.get_all("Module Onboarding"):
		frappe.db.set_value("Module Onboarding", modu, "is_complete", 1)
	frappe.set_value("Website Settings", "Website Settings", "home_page", "login")
	frappe.db.commit()
	ensure_cloud_storage_installed()
	create_test_data()


def ensure_cloud_storage_installed():
	if "cloud_storage" not in frappe.get_installed_apps():
		frappe.throw(
			"cloud_storage is not installed. Run: bench --site <site> install-app cloud_storage"
		)


def create_test_data():
	ensure_module_def("Automation")
	remove_stale_retrieve_file_rows()
	frappe.db.commit()


def ensure_module_def(module_name):
	if frappe.db.exists("Module Def", module_name):
		return

	frappe.get_doc(
		{
			"doctype": "Module Def",
			"module_name": module_name,
			"app_name": "Cloud Storage",
		}
	).insert(ignore_permissions=True)


def remove_stale_retrieve_file_rows():
	for name in frappe.get_all(
		"File",
		filters={"file_url": ["like", "%/api/method/retrieve%"]},
		pluck="name",
	):
		frappe.delete_doc("File", name, ignore_permissions=True, force=True)
