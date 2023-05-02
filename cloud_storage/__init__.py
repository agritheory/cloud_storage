__version__ = "14.0.0"


import frappe.desk.form.load
from frappe.query_builder import DocType


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
