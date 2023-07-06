frappe.provide('frappe.ui')

import { Attachments } from '../../../../frappe/frappe/public/js/frappe/form/sidebar/attachments'

$(window).on('hashchange', page_changed)
$(window).on('load', page_changed)

function page_changed(event) {
	frappe.after_ajax(() => {
		const route = frappe.get_route()
		if (route && route.length > 0 && route[0] == 'Form') {
			frappe.ui.form.on(route[1], {
				refresh: frm => {
					disallow_attachment_delete(frm)
				},
			})
			if (event.type == 'load') {
				disallow_attachment_delete(cur_frm)
			}
		}
	})
}

function disallow_attachment_delete(frm) {
	if (frm.doc.docstatus == 1) {
		frm.$wrapper.find('.attachment-row').find('.remove-btn').hide()
	}
}

class CloudStorageAttachments extends Attachments {
	constructor(opts) {
		super()
	}
	new_attachment(fieldname) {
		if (this.dialog) {
			// remove upload dialog
			this.dialog.$wrapper.remove()
		}
		console.log('new attachment')
		const restrictions = {}
		if (this.frm.meta.max_attachments) {
			restrictions.max_number_of_files = this.frm.meta.max_attachments - this.frm.attachments.get_attachments().length
		}

		new frappe.ui.FileUploader({
			doctype: this.frm.doctype,
			docname: this.frm.docname,
			frm: this.frm,
			folder: 'Home/Attachments',
			on_success: async file_doc => {
				await this.version_check()
				this.attachment_uploaded(file_doc)
			},
			restrictions,
			make_attachments_public: this.frm.meta.make_attachments_public,
		})
	}
	async version_check() {
		console.log('version check')
		return
	}
}

frappe.ui.form.Attachments = CloudStorageAttachments
