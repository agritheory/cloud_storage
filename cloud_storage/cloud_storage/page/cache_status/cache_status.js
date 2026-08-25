frappe.pages['cache-status'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Cloud Storage Cache Status',
		single_column: true,
	});

	page.add_inner_button('Refresh', () => render(page));
	render(page);
};

function format_bytes(bytes) {
	if (bytes === null || bytes === undefined) return '-';
	const units = ['B', 'KB', 'MB', 'GB', 'TB'];
	let value = bytes;
	let unit = 0;
	while (value >= 1024 && unit < units.length - 1) {
		value /= 1024;
		unit += 1;
	}
	return `${value.toFixed(1)} ${units[unit]}`;
}

function render(page) {
	frappe.call({
		method: 'cloud_storage.cloud_storage.page.cache_status.cache_status.get_status',
		callback: (r) => {
			const data = r.message;
			$(page.body).empty();
			$(page.body).append(render_config(data.config));
			$(page.body).append(render_health(data.health));
			$(page.body).append(render_cache(data.cache));
			$(page.body).append(render_recent(data.recent));
		},
	});
}

function render_config(config) {
	return `
		<div class="cache-status-section">
			<h4>Configuración (site_config.json)</h4>
			<table class="table table-bordered">
				<tr><td>local_cache_enabled</td><td>${config.local_cache_enabled}</td></tr>
				<tr><td>max_cache_size_gb</td><td>${config.max_cache_size_gb}</td></tr>
				<tr><td>emergency_cache_size_gb</td><td>${config.emergency_cache_size_gb}</td></tr>
				<tr><td>cache_retention_minutes</td><td>${config.cache_retention_minutes}</td></tr>
				<tr><td>warm_on_read</td><td>${config.warm_on_read}</td></tr>
				<tr><td>replication_max_retries</td><td>${config.replication_max_retries}</td></tr>
			</table>
		</div>
	`;
}

function render_health(health) {
	return `
		<div class="cache-status-section">
			<h4>Cloud Storage Health</h4>
			<table class="table table-bordered">
				<tr><td>status</td><td>${health.status || '-'}</td></tr>
				<tr><td>consecutive_failures</td><td>${health.consecutive_failures ?? '-'}</td></tr>
				<tr><td>failure_threshold</td><td>${health.failure_threshold ?? '-'}</td></tr>
				<tr><td>degraded_since</td><td>${health.degraded_since || '-'}</td></tr>
				<tr><td>last_error</td><td>${health.last_error || '-'}</td></tr>
				<tr><td>last_check_at</td><td>${health.last_check_at || '-'}</td></tr>
			</table>
		</div>
	`;
}

function render_cache(cache) {
	if (!cache) {
		return `
			<div class="cache-status-section">
				<h4>Local File Cache (SQLite)</h4>
				<p>local_cache_enabled está en false — no hay índice de caché activo.</p>
			</div>
		`;
	}
	return `
		<div class="cache-status-section">
			<h4>Local File Cache (SQLite)</h4>
			<table class="table table-bordered">
				<tr><td>Filas totales</td><td>${cache.total_rows}</td></tr>
				<tr><td>Filas vivas</td><td>${cache.live_rows}</td></tr>
				<tr><td>Sin replicar</td><td>${cache.unreplicated_rows}</td></tr>
				<tr><td>Pendientes de borrado</td><td>${cache.pending_delete_rows}</td></tr>
				<tr><td>Bytes cacheados</td><td>${format_bytes(cache.cached_bytes_total)} / ${format_bytes(cache.max_cache_size_bytes)}</td></tr>
				<tr><td>Bytes sin replicar (techo de emergencia)</td><td>${format_bytes(cache.unevictable_bytes_total)} / ${format_bytes(cache.emergency_cache_size_bytes)}</td></tr>
			</table>
		</div>
	`;
}

function render_recent(recent) {
	if (!recent || !recent.length) {
		return `
			<div class="cache-status-section">
				<h4>Archivos recientes</h4>
				<p>Sin registros.</p>
			</div>
		`;
	}
	const rows = recent
		.map(
			(row) => `
				<tr>
					<td>${row.file}</td>
					<td>${format_bytes(row.file_size)}</td>
					<td>${row.replicated ? 'Sí' : 'No'}</td>
					<td>${row.pending_delete ? 'Sí' : 'No'}</td>
					<td>${row.accessed_at || '-'}</td>
				</tr>
			`
		)
		.join('');
	return `
		<div class="cache-status-section">
			<h4>Archivos recientes (últimos 20 por acceso)</h4>
			<table class="table table-bordered">
				<thead>
					<tr><th>File</th><th>Tamaño</th><th>Replicado</th><th>Pendiente de borrado</th><th>Último acceso</th></tr>
				</thead>
				<tbody>${rows}</tbody>
			</table>
		</div>
	`;
}
