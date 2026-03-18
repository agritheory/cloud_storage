// Copyright (c) 2025, AgriTheory and contributors
// For license information, please see license.txt

const THREE_D_EXTENSIONS = ['obj', 'glb', 'gltf', 'fbx', 'dae', 'ply', 'stl']

function is_3d_file(frm) {
	const file_string = (frm.doc.file_type || frm.doc.file_name || '').toLowerCase()
	return THREE_D_EXTENSIONS.some(ext => file_string.endsWith(ext))
}

frappe.ui.form.on('File', {
	refresh: frm => {
		if (!frm.doc.is_folder) {
			// Share buttons
			frm.add_custom_button(__('Get Sharing Link', 'Share'), () => get_sharing_link(frm, false))
			if (frm.doc.sharing_link) {
				frm.add_custom_button(__('Reset Sharing Link', 'Share'), () => get_sharing_link(frm, true))
			}
		}

		let file_string = frm.doc.file_type || frm.doc.file_name
		file_string = file_string.toLowerCase()

		if (['doc', 'docx'].some(ext => file_string.includes(ext))) {
			frm.trigger('preview_doc_content')
		} else if (['ppt', 'pptx', 'odp', 'key'].some(ext => file_string.includes(ext))) {
			frm.trigger('preview_file_as_pdf')
		} else if (is_3d_file(frm)) {
			frm.trigger('preview_3d')
		}
	},

	preview_3d: function (frm) {
		// Add "Preview 3D" button in the form toolbar
		frm.add_custom_button(__('Preview 3D'), () => {
			launch_3d_modal(frm.doc.file_url, frm.doc.file_name)
		})
	},

	preview_file_as_pdf: async function (frm) {
		const response = await frm.call('get_pdf_preview')
		let pdf_content = response.message
		if (pdf_content) {
			const byteCharacters = atob(pdf_content)
			const byteNumbers = new Array(byteCharacters.length)
			for (let i = 0; i < byteCharacters.length; i++) {
				byteNumbers[i] = byteCharacters.charCodeAt(i)
			}
			const byteArray = new Uint8Array(byteNumbers)
			const blob = new Blob([byteArray], { type: 'application/pdf' })
			const url = URL.createObjectURL(blob)
			const field = frm.get_field('preview_html')
			const $preview = $(`<div class="img_preview">
			   <object style="background:#323639;" width="100%">
				   <embed
					   style="background:#323639;"
					   width="100%"
					   height="1190"
					   src="${url}" type="application/pdf"
				   >
			   </object>
		   </div>`)
			field.$wrapper.html($preview)
			frm.toggle_display('preview', true)
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
		const file_url = frm.doc.file_url.replace(/#/g, '%23')

		if (frappe.utils.is_image_file(file_url)) {
			$preview = $(`<div class="img_preview">
				<img class="img-responsive" src="${file_url}"
					onerror="${frm.toggle_display('preview', false)}" />
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
					<embed style="background:#323639;" width="100%" height="1190"
						src="${file_url}" type="application/pdf">
				</object>
			</div>`)
		} else if (file_extension === 'mp3') {
			$preview = $(`<div class="img_preview">
				<audio width="480" height="60" controls>
					<source src="${file_url}" type="audio/mpeg">
					${__('Your browser does not support the audio element.')}
				</audio>
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

// ─── 3D Modal ────────────────────────────────────────────────────────────────

const THREE_CDN = 'https://cdn.jsdelivr.net/npm/three@0.158.0/build/three.module.js'
const CDN = 'https://cdn.jsdelivr.net/npm/three@0.158.0/examples/jsm'

// Inject importmap so loaders can resolve bare "three" specifier
function inject_importmap() {
	if (document.querySelector('script[type="importmap"]')) return
	const map = document.createElement('script')
	map.type = 'importmap'
	map.textContent = JSON.stringify({
		imports: {
			three: THREE_CDN,
			'three/addons/': `${CDN}/`,
		},
	})
	document.head.prepend(map)
}

async function launch_3d_modal(file_url, filename) {
	inject_importmap()
	const dialog = new frappe.ui.Dialog({
		title: `🧊 ${filename}`,
		size: 'extra-large',
	})
	dialog.show()

	// Style the dialog body as a 3D viewport
	const $body = dialog.$wrapper.find('.modal-body')
	$body.css({ padding: 0, background: '#1a1a2e', 'min-height': '70vh' })

	const container = document.createElement('div')
	container.style.cssText = 'width:100%;height:70vh;position:relative;'
	$body[0].appendChild(container)

	// Loading indicator
	const $loading = $(
		'<div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#aaa;font-size:16px;z-index:1;">Loading 3D model...</div>'
	)
	$(container).append($loading)

	try {
		// Extract S3 key from the retrieve URL e.g. /api/method/retrieve?key=Item/Kiwi/cube.obj
		const key = new URLSearchParams(file_url.split('?')[1]).get('key')
		const proxy_url = `/api/method/cloud_storage.cloud_storage.overrides.file.proxy_file?key=${encodeURIComponent(key)}`

		// Fetch file server-side through Frappe proxy (avoids S3 CORS)
		const response = await fetch(proxy_url, {
			credentials: 'same-origin',
			headers: { 'X-Frappe-CSRF-Token': frappe.csrf_token },
		})
		if (!response.ok) throw new Error(`Failed to fetch file: ${response.status}`)
		const blob = await response.blob()
		const blob_url = URL.createObjectURL(blob)

		const THREE = await import(THREE_CDN)
		const { OrbitControls } = await import(`${CDN}/controls/OrbitControls.js`)

		const w = container.clientWidth
		const h = container.clientHeight

		// Scene
		const scene = new THREE.Scene()
		scene.background = new THREE.Color(0x1a1a2e)
		scene.add(new THREE.GridHelper(10, 20, 0x444444, 0x333333))

		// Camera
		const camera = new THREE.PerspectiveCamera(60, w / h, 0.01, 10000)
		camera.position.set(3, 3, 3)

		// Renderer
		const renderer = new THREE.WebGLRenderer({ antialias: true })
		renderer.setSize(w, h)
		renderer.setPixelRatio(window.devicePixelRatio)
		renderer.shadowMap.enabled = true
		container.appendChild(renderer.domElement)

		// Controls
		const controls = new OrbitControls(camera, renderer.domElement)
		controls.enableDamping = true
		controls.dampingFactor = 0.05

		// Lights
		scene.add(new THREE.AmbientLight(0xffffff, 0.6))
		const dir = new THREE.DirectionalLight(0xffffff, 1)
		dir.position.set(5, 10, 5)
		dir.castShadow = true
		scene.add(dir)
		scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 0.4))

		// Load model using blob URL (avoids CORS/auth issues with S3)
		const ext = filename.split('.').pop().toLowerCase()
		const object = await load_3d_model(THREE, ext, blob_url)

		// Center & fit camera
		const box = new THREE.Box3().setFromObject(object)
		const size = box.getSize(new THREE.Vector3()).length()
		const center = box.getCenter(new THREE.Vector3())
		object.position.sub(center)
		camera.near = size / 100
		camera.far = size * 100
		camera.position.set(size, size, size)
		camera.lookAt(0, 0, 0)
		camera.updateProjectionMatrix()
		controls.maxDistance = size * 10
		controls.update()

		scene.add(object)
		$loading.remove()

		// Animate
		let running = true
		function animate() {
			if (!running) return
			requestAnimationFrame(animate)
			controls.update()
			renderer.render(scene, camera)
		}
		animate()

		// Resize handler
		const on_resize = () => {
			const w = container.clientWidth
			const h = container.clientHeight
			camera.aspect = w / h
			camera.updateProjectionMatrix()
			renderer.setSize(w, h)
		}
		window.addEventListener('resize', on_resize)

		// Cleanup on dialog close
		dialog.$wrapper.on('hidden.bs.modal', () => {
			running = false
			renderer.dispose()
			URL.revokeObjectURL(blob_url)
			window.removeEventListener('resize', on_resize)
		})
	} catch (e) {
		$loading.text('Failed to load 3D model: ' + e.message).css('color', '#ff6b6b')
		console.error('3D Preview error:', e)
	}
}

async function load_3d_model(THREE, ext, file_url) {
	switch (ext) {
		case 'glb':
		case 'gltf': {
			const { GLTFLoader } = await import(`${CDN}/loaders/GLTFLoader.js`)
			const { DRACOLoader } = await import(`${CDN}/loaders/DRACOLoader.js`)
			const draco = new DRACOLoader()
			draco.setDecoderPath('https://www.gstatic.com/draco/versioned/decoders/1.5.6/')
			const loader = new GLTFLoader()
			loader.setDRACOLoader(draco)
			return new Promise((res, rej) => loader.load(file_url, g => res(g.scene), undefined, rej))
		}
		case 'obj': {
			const { OBJLoader } = await import(`${CDN}/loaders/OBJLoader.js`)
			return new Promise((res, rej) => new OBJLoader().load(file_url, res, undefined, rej))
		}
		case 'stl': {
			const { STLLoader } = await import(`${CDN}/loaders/STLLoader.js`)
			return new Promise((res, rej) =>
				new STLLoader().load(
					file_url,
					geometry => {
						const mat = new THREE.MeshStandardMaterial({ color: 0x888888, metalness: 0.3, roughness: 0.6 })
						res(new THREE.Mesh(geometry, mat))
					},
					undefined,
					rej
				)
			)
		}
		case 'ply': {
			const { PLYLoader } = await import(`${CDN}/loaders/PLYLoader.js`)
			return new Promise((res, rej) =>
				new PLYLoader().load(
					file_url,
					geometry => {
						geometry.computeVertexNormals()
						const mat = new THREE.MeshStandardMaterial({
							color: 0x888888,
							vertexColors: geometry.hasAttribute('color'),
						})
						res(new THREE.Mesh(geometry, mat))
					},
					undefined,
					rej
				)
			)
		}
		case 'fbx': {
			const { FBXLoader } = await import(`${CDN}/loaders/FBXLoader.js`)
			return new Promise((res, rej) => new FBXLoader().load(file_url, res, undefined, rej))
		}
		case 'dae': {
			const { ColladaLoader } = await import(`${CDN}/loaders/ColladaLoader.js`)
			return new Promise((res, rej) => new ColladaLoader().load(file_url, c => res(c.scene), undefined, rej))
		}
		default:
			throw new Error(`Unsupported format: .${ext}`)
	}
}
