frappe.ui.form.on("File", {
	refresh: frm => {
		if (!frm.doc.is_folder) {
			// add download button
			frm.add_custom_button(__("Get Sharing Link", "Share"), () => get_sharing_link(frm))
			if(frm.doc.sharing_link){
				frm.add_custom_button(__("Reset Sharing Link", "Share"), () => get_sharing_link(frm, true))
			}
		}
	}
})

function get_sharing_link(frm){
	frappe.xcall('cloud_storage.cloud_storage.overrides.file.get_sharing_link', {docname: frm.doc.name, reset: true})
	.then(r => { 
		frappe.msgprint(r, __('Sharing Link'))
	})
}
