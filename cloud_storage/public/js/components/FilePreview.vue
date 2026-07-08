<template>
	<div class="file-preview">
		<div style="color:red">
🔥 FILE PREVIEW 12345
</div>
		<div style="color:red">FILE PREVIEW WORKING</div>
		<div style="color:red">TEST COMPONENT</div>

		<!-- 🔹 Non-3D files -->
		<div class="file-icon" v-if="!is_3d">
			<img v-if="is_image" :src="src" :alt="file.name" />
			<div v-else v-html="frappe.utils.icon('file', 'md')"></div>
		</div>

		<!-- 🔹 3D Preview -->
		<ThreePreview
			v-else
			:file_url="resolved_url"
			:filename="file.name"
			:is_private="file.private"
		/>

		<!-- 🔹 File Info -->
		<div class="file-info">
			<div class="file-name">{{ file.name }}</div>

			<div class="file-actions">
				<button class="btn btn-xs" @click="$emit('remove')">✕</button>
				<button class="btn btn-xs" @click="$emit('toggle_private')">
					{{ file.private ? '🔒' : '🌐' }}
				</button>
			</div>
		</div>

	</div>
</template>

<script>
import ThreePreview from './ThreePreview.vue'

export default {
	props: ['file'],

	computed: {
		// ✅ detect image
		is_image() {
			return this.file?.file_obj?.type?.startsWith('image')
		},

		// ✅ detect 3D
		is_3d() {
			if (!this.file?.name) return false
			return ['.obj','.glb','.gltf','.fbx','.dae','.ply','.stl']
				.some(ext => this.file.name.toLowerCase().endsWith(ext))
		},

		// ✅ resolve correct URL (IMPORTANT)
		resolved_url() {
			// before upload
			if (this.file.file_obj) {
				return URL.createObjectURL(this.file.file_obj)
			}

			// after upload
			if (this.file.doc?.file_url) {
				return this.file.doc.file_url
			}

			return null
		},

		// for images
		src() {
			if (this.file.file_obj) {
				return URL.createObjectURL(this.file.file_obj)
			}
			return this.file.doc?.file_url
		}
	}
}
</script>

<style scoped>
.file-preview {
	display: flex;
	flex-direction: column;
	align-items: center;
	padding: 10px;
	border: 1px solid var(--border-color);
	border-radius: 8px;
	background: var(--bg-color);
}

.file-icon {
	width: 200px;
	height: 200px;
	display: flex;
	align-items: center;
	justify-content: center;
	background: #f5f5f5;
	border-radius: 6px;
	overflow: hidden;
}

.file-icon img {
	max-width: 100%;
	max-height: 100%;
	object-fit: contain;
}

.file-info {
	margin-top: 8px;
	text-align: center;
}

.file-name {
	font-size: 12px;
	word-break: break-all;
}

.file-actions {
	margin-top: 5px;
	display: flex;
	gap: 5px;
}
</style>