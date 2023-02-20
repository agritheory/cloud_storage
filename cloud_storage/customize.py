import json
from pathlib import Path

import frappe 
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

def load_customizations():
	print('Loading Cloud Storage customizations')
	customizations_directory = Path().cwd().parent / 'apps' / 'cloud_storage' / 'cloud_storage' / 'cloud_storage' / 'custom'
	files = list(customizations_directory.glob('**/*.json'))
	for file in files:
		customizations = json.loads(Path(file).read_text())
		for field in customizations.get('custom_fields'):
			existing_field = frappe.get_value('Custom Field', field.get('name'))
			custom_field = frappe.get_doc("Custom Field", field.get('name')) if existing_field else frappe.new_doc('Custom Field')
			field.pop('modified')
			{custom_field.set(key, value) for key, value in field.items()}
			custom_field.flags.ignore_permissions = True
			custom_field.flags.ignore_version = True
			custom_field.save()
		for prop in customizations.get('property_setters'):
			property_setter = frappe.get_doc({
				"name": prop.get('name'),
				"doctype": "Property Setter",
				"doctype_or_field": prop.get('doctype_or_field'),
				"doc_type": prop.get('doc_type'),
				"field_name": prop.get('field_name'),
				"property": prop.get('property'),
				"value": prop.get('value'),
				"property_type": prop.get('property_type')
			})
			property_setter.flags.ignore_permissions = True
			property_setter.insert()
