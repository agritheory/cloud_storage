$(window).on("hashchange", page_changed);
$(window).on("load", page_changed);

function page_changed(event) {
	frappe.after_ajax(() => {
		const route = frappe.get_route();
		if (route && route.length > 0 && route[0] == "Form") {
			frappe.ui.form.on(route[1], {
				refresh: (frm) => {
					disallow_attachment_delete(frm);
				},
			});
			if (event.type == "load") {
				disallow_attachment_delete(cur_frm);
			}
		}
	});
}

function disallow_attachment_delete(frm) {
	if (frm.doc.docstatus == 1) {
		frm.$wrapper.find(".attachment-row").find(".remove-btn").hide();
	}
}
