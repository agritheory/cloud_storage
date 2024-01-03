frappe.ui.form.on('File', {
	refresh: frm => {
		if (!frm.doc.is_folder) {
			// add download button
			frm.add_custom_button(__('Get Sharing Link', 'Share'), () => get_sharing_link(frm, false))
			if (frm.doc.sharing_link) {
				frm.add_custom_button(__('Reset Sharing Link', 'Share'), () => get_sharing_link(frm, true))
			}
		}

		let file_string = frm.doc.file_type || frm.doc.file_name
		file_string = file_string.toLowerCase()
		if (['doc', 'docx'].includes(file_string)) {
			frm.trigger('preview_doc_content')
		}
	},

	preview_doc_content: async function (frm) {
		const response = await frm.call('get_content')
		let file_content = response.message
		if (file_content) {
			const field = frm.get_field('preview_html')
			const container = field.wrapper

			frappe.Docx.renderAsync(file_content, container, container, {
				ignoreLastRenderedPageBreak: false,
				experimental: true,
			})

			frm.toggle_display('preview', true)
		}
	},
})

function get_sharing_link(frm, reset) {
	frappe
		.xcall('cloud_storage.cloud_storage.overrides.file.get_sharing_link', { docname: frm.doc.name, reset: reset })
		.then(r => {
			frappe.msgprint(r, __('Sharing Link'))
		})
}
