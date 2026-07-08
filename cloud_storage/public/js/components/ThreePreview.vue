<template>
	<div ref="container" class="three-container"></div>
</template>

<script>
export default {
	props: ['file_url'],

	async mounted() {
		console.log("ThreePreview mounted:", this.file_url)

		// ✅ Load THREE from CDN
		const THREE = await import('https://unpkg.com/three@0.158.0/build/three.module.js')

		// ✅ Load OrbitControls
		const { OrbitControls } = await import(
			'https://unpkg.com/three@0.158.0/examples/jsm/controls/OrbitControls.js'
		)

		// ✅ Load OBJLoader
		const { OBJLoader } = await import(
			'https://unpkg.com/three@0.158.0/examples/jsm/loaders/OBJLoader.js'
		)

		const container = this.$refs.container

		// Scene
		const scene = new THREE.Scene()
		scene.background = new THREE.Color(0x222222)

		// Camera
		const camera = new THREE.PerspectiveCamera(
			75,
			container.clientWidth / container.clientHeight,
			0.1,
			1000
		)
		camera.position.set(2, 2, 2)

		// Renderer
		const renderer = new THREE.WebGLRenderer({ antialias: true })
		renderer.setSize(container.clientWidth, container.clientHeight)
		container.appendChild(renderer.domElement)

		// Controls
		const controls = new OrbitControls(camera, renderer.domElement)

		// Lights
		const light = new THREE.DirectionalLight(0xffffff, 1)
		light.position.set(5, 5, 5)
		scene.add(light)

		scene.add(new THREE.AmbientLight(0x404040))

		// Load OBJ
		const loader = new OBJLoader()
		loader.load(this.file_url, (object) => {

			// Center model
			const box = new THREE.Box3().setFromObject(object)
			const center = box.getCenter(new THREE.Vector3())
			object.position.sub(center)

			const size = box.getSize(new THREE.Vector3()).length()
			camera.position.set(size, size, size)

			scene.add(object)
		})

		// Animate
		function animate() {
			requestAnimationFrame(animate)
			controls.update()
			renderer.render(scene, camera)
		}
		animate()
	}
}
</script>

<style scoped>
.three-container {
	width: 100%;
	height: 200px;   /* 🔥 THIS IS WHY IT WAS INVISIBLE */
	background: #111;
	border-radius: 6px;
}
</style>