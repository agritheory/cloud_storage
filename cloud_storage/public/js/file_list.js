// Copyright (c) 2025, AgriTheory and contributors
// For license information, please see license.txt

frappe.listview_settings['File'] = {
	get_indicator: function (doc) {
		if (doc.custom_status === 'Latest') {
			return [__('Latest'), 'green', 'custom_status,=,Latest']
		}
		if (doc.custom_status === 'Older Version') {
			return [__('Outdated'), 'orange', 'custom_status,=,Older Version']
		}
	},
}
