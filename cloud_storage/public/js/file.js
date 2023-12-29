frappe.ui.form.on('File', {
	refresh: frm => {
		if (!frm.doc.is_folder) {
			// add download button
			frm.add_custom_button(__('Get Sharing Link', 'Share'), () => get_sharing_link(frm, false))
			if (frm.doc.sharing_link) {
				frm.add_custom_button(__('Reset Sharing Link', 'Share'), () => get_sharing_link(frm, true))
			}
		}

		let file_extension = frm.doc.file_type.toLowerCase()
		if (['doc', 'docx'].includes(file_extension)) {
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

	preview_file: function (frm) {
		let $preview = ''
		const file_extension = frm.doc.file_type.toLowerCase()
		// Cloud Storage: replace # with %23 in PDFs
		const file_url = frm.doc.file_url.replace(/#/g, '%23')

		if (frappe.utils.is_image_file(file_url)) {
			$preview = $(`<div class="img_preview">
				<img
					class="img-responsive"
					src="${file_url}"
					onerror="${frm.toggle_display('preview', false)}"
				/>
			</div>`)
		} else if (frappe.utils.is_video_file(file_url)) {
			$preview = $(`<div class="img_preview">
				<video width="480" height="320" controls>
					<source src="${file_url}">
					${__('Your browser does not support the video element.')}
				</video>
			</div>`)
		} else if (file_extension === 'pdf') {
			$preview = $(`<div class="img_preview">
				<object style="background:#323639;" width="100%">
					<embed
						style="background:#323639;"
						width="100%"
						height="1190"
						src="${file_url}" type="application/pdf"
					>
				</object>
			</div>`)
		} else if (file_extension === 'mp3') {
			$preview = $(`<div class="img_preview">
				<audio width="480" height="60" controls>
					<source src="${file_url}" type="audio/mpeg">
					${__('Your browser does not support the audio element.')}
				</audio >
			</div>`)
		}

		if ($preview) {
			frm.toggle_display('preview', true)
			frm.get_field('preview_html').$wrapper.html($preview)
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
