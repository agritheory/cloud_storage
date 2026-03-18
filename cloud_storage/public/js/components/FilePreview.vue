<template>
	<div class="file-preview-outline">
		<div class="file-preview">
			<!-- File icon / image thumbnail -->
			<div class="file-icon">
				<img v-if="is_image" :src="src" :alt="file.name" />
				<div class="fallback" v-else v-html="frappe.utils.icon('file', 'md')"></div>
			</div>

			<!-- File info -->
			<div class="file-meta">
				<div>
					<a class="flex" :href="file.doc.file_url" v-if="file.doc" target="_blank">
						<span class="file-name">{{ file.name }}</span>
					</a>
					<span class="file-name" v-else>{{ file.name }}</span>
				</div>
				<div>
					<span class="file-size">{{ file_size }}</span>
				</div>
				<div class="flex config-area">
					<label v-if="allow_toggle_optimize" class="frappe-checkbox">
						<input type="checkbox" :checked="optimize" @change="$emit('toggle_optimize')" />
						{{ __('Optimize') }}
					</label>
					<label v-if="allow_toggle_private" class="frappe-checkbox">
						<input type="checkbox" :checked="file.private" @change="$emit('toggle_private')" />
						{{ __('Private') }}
					</label>
				</div>
			</div>

			<!-- Actions -->
			<div class="file-actions">
				<!-- Upload spinner -->
				<div v-if="file.uploading && !uploaded && !file.failed" class="upload-spinner"></div>
				<div v-if="uploaded" v-html="frappe.utils.icon('solid-success', 'lg')"></div>
				<div v-if="file.failed" v-html="frappe.utils.icon('solid-error', 'lg')"></div>
				<div class="file-action-buttons">
					<!-- Crop -->
					<button
						v-if="is_cropable"
						class="btn muted"
						@click="$emit('toggle_image_cropper')"
						v-html="frappe.utils.icon('crop', 'md')"></button>
					<!-- Remove -->
					<button
						v-if="!uploaded && !file.uploading && !file.failed"
						class="btn muted"
						@click="$emit('remove')"
						v-html="frappe.utils.icon('delete', 'md')"></button>
				</div>
			</div>
		</div>

		<!-- Alerts -->
		<div v-if="file.error_message" class="alert alert-danger mb-0 mt-2" role="alert">
			{{ file.error_message }}
		</div>
		<div
			v-if="!file.private && !file.error_message && !uploaded && !file.failed"
			class="alert alert-warning mb-0"
			role="alert">
			{{
				__(
					'This file is public and can be accessed by anyone, even without logging in. Mark it private to limit access.'
				)
			}}
		</div>
	</div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'

const emit = defineEmits(['toggle_optimize', 'toggle_private', 'toggle_image_cropper', 'remove'])

const props = defineProps({
	file: Object,
	allow_toggle_private: { default: true },
	allow_toggle_optimize: { default: true },
})

const src = ref(null)
const optimize = ref(props.file.optimize)

const is_image = computed(() => props.file.file_obj?.type?.startsWith('image'))
const uploaded = computed(() => props.file.request_succeeded)
const file_size = computed(() => frappe.form.formatters.FileSize(props.file.file_obj?.size))
const allow_toggle_optimize = computed(() => {
	const is_svg = props.file.file_obj?.type === 'image/svg+xml'
	return props.allow_toggle_optimize && is_image.value && !is_svg && !uploaded.value && !props.file.failed
})
const allow_toggle_private = computed(() => {
	return props.allow_toggle_private && !uploaded.value && !props.file.failed
})
const is_cropable = computed(() => {
	const croppable = ['image/jpeg', 'image/png']
	return !uploaded.value && !props.file.uploading && !props.file.failed && croppable.includes(props.file.file_obj?.type)
})

onMounted(() => {
	if (is_image.value && window.FileReader) {
		const fr = new FileReader()
		fr.onload = () => (src.value = fr.result)
		fr.readAsDataURL(props.file.file_obj)
	}
})
</script>

<style scoped>
.file-preview-outline {
	padding: 0.75rem;
	border: 1px solid transparent;
	display: flex;
	flex-direction: column;
}
.file-preview {
	display: flex;
	align-items: center;
	flex-direction: row;
	gap: 12px;
}
.file-preview-outline + .file-preview-outline {
	border-top-color: var(--border-color);
}
.file-preview-outline:hover {
	background-color: var(--bg-color);
	border-color: var(--dark-border-color);
	border-radius: var(--border-radius);
}
.file-icon {
	border-radius: var(--border-radius);
	width: 2.625rem;
	height: 2.625rem;
	overflow: hidden;
	flex-shrink: 0;
}
.file-icon img {
	width: 100%;
	height: 100%;
	object-fit: cover;
}
.file-icon .fallback {
	width: 100%;
	height: 100%;
	display: flex;
	align-items: center;
	justify-content: center;
	border: 1px solid var(--border-color);
	border-radius: var(--border-radius);
}
.file-meta {
	flex: 1;
	min-width: 0;
}
.file-name {
	font-size: var(--text-base);
	font-weight: var(--text-bold);
	color: var(--text-color);
	display: -webkit-box;
	-webkit-line-clamp: 1;
	-webkit-box-orient: vertical;
	overflow: hidden;
}
.file-size {
	font-size: var(--text-sm);
	color: var(--text-light);
}
.file-actions {
	width: auto;
	flex-shrink: 0;
	margin-left: auto;
	display: flex;
	align-items: center;
	gap: 6px;
}
.file-action-buttons {
	display: flex;
	align-items: center;
	gap: 4px;
}
.muted {
	opacity: 0.5;
	transition: 0.3s;
	padding: var(--padding-xs);
	box-shadow: none;
}
.muted:hover {
	opacity: 1;
}
.frappe-checkbox {
	font-size: var(--text-sm);
	color: var(--text-light);
	display: flex;
	align-items: center;
	padding-top: 0.25rem;
}
.config-area {
	gap: 0.5rem;
}

/* Simple upload spinner replacing ProgressRing */
.upload-spinner {
	width: 24px;
	height: 24px;
	border: 3px solid var(--gray-200);
	border-top-color: var(--primary-color);
	border-radius: 50%;
	animation: spin 0.8s linear infinite;
	flex-shrink: 0;
}
@keyframes spin {
	to {
		transform: rotate(360deg);
	}
}
</style>
