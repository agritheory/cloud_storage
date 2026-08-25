frappe.pages['cache-status'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Cloud Storage Cache Status',
		single_column: true,
	});

	page.add_inner_button('Refresh', () => render(page));
	render(page);
};

const STYLE = `
	<style>
		.cache-status-section { margin-bottom: 24px; }
		.cache-status-section h4 { margin-bottom: 10px; }
		.cache-status-stats {
			display: grid;
			grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
			gap: 12px;
		}
		.cache-status-stat {
			border: 1px solid var(--border-color);
			border-radius: var(--border-radius);
			padding: 12px 14px;
		}
		.cache-status-stat .value { font-size: 20px; font-weight: 600; }
		.cache-status-stat .label { font-size: 12px; color: var(--text-muted); }
		.cache-status-config { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; }
		.cache-status-config dt { color: var(--text-muted); }
		.cache-status-config dd { margin: 0; }
	</style>
`;

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

function format_datetime(value) {
	if (!value) return '-';
	const normalized = value.replace(' ', 'T').replace(/(\.\d{3})\d*$/, '$1');
	const date = new Date(normalized);
	if (isNaN(date.getTime())) return value;
	const date_part = date.toLocaleDateString('en-US');
	const time_part = date.toLocaleTimeString('en-US', { hour12: false });
	return `${date_part}, ${time_part}`;
}

function indicator_color(status) {
	return status === 'Degraded' ? 'red' : 'green';
}

function render(page) {
	frappe.call({
		method: 'cloud_storage.cloud_storage.page.cache_status.cache_status.get_status',
		callback: (r) => {
			const data = r.message;
			if (data.health) {
				page.set_indicator(data.health.status, indicator_color(data.health.status));
			} else {
				page.clear_indicator();
			}
			$(page.body).html(STYLE);
			$(page.body).append(render_stats(data.cache));
			$(page.body).append(render_health(data.health));
			$(page.body).append(render_config(data.config));
			$(page.body).append(render_recent(data.recent));
		},
	});
}

function render_stats(cache) {
	if (!cache) {
		return `
			<div class="cache-status-section">
				<p>Local cache is disabled — no active index.</p>
			</div>
		`;
	}
	const stats = [
		['Cached', `${format_bytes(cache.cached_bytes_total)} / ${format_bytes(cache.max_cache_size_bytes)}`],
		['Unreplicated (emergency ceiling)', `${format_bytes(cache.unevictable_bytes_total)} / ${format_bytes(cache.emergency_cache_size_bytes)}`],
		['Live entries', cache.live_rows],
		['Unreplicated entries', cache.unreplicated_rows],
		['Pending delete', cache.pending_delete_rows],
	];
	const cards = stats
		.map(
			([label, value]) => `
				<div class="cache-status-stat">
					<div class="value">${value}</div>
					<div class="label">${label}</div>
				</div>
			`
		)
		.join('');
	return `<div class="cache-status-section"><div class="cache-status-stats">${cards}</div></div>`;
}

function render_health(health) {
	if (!health) return '';
	return `
		<div class="cache-status-section">
			<h4>Health</h4>
			<dl class="cache-status-config">
				<dt>Consecutive failures</dt><dd>${health.consecutive_failures ?? '-'}</dd>
				<dt>Degraded since</dt><dd>${format_datetime(health.degraded_since)}</dd>
				<dt>Last check</dt><dd>${format_datetime(health.last_check_at)}</dd>
				<dt>Last error</dt><dd>${health.last_error || '-'}</dd>
			</dl>
		</div>
	`;
}

function render_config(config) {
	return `
		<div class="cache-status-section">
			<h4>Configuration (site_config.json)</h4>
			<dl class="cache-status-config">
				<dt>local_cache_enabled</dt><dd>${config.local_cache_enabled}</dd>
				<dt>max_cache_size_gb</dt><dd>${config.max_cache_size_gb}</dd>
				<dt>emergency_cache_size_gb</dt><dd>${config.emergency_cache_size_gb}</dd>
				<dt>cache_retention_minutes</dt><dd>${config.cache_retention_minutes}</dd>
				<dt>warm_on_read</dt><dd>${config.warm_on_read}</dd>
				<dt>replication_max_retries</dt><dd>${config.replication_max_retries}</dd>
				<dt>failure_threshold</dt><dd>${config.failure_threshold}</dd>
			</dl>
		</div>
	`;
}

function render_recent(recent) {
	if (!recent || !recent.length) {
		return `
			<div class="cache-status-section">
				<h4>Recent files</h4>
				<p>No entries.</p>
			</div>
		`;
	}
	const rows = recent
		.map(
			(row) => `
				<tr>
					<td><a href="${frappe.utils.get_form_link('File', row.file)}" target="_blank" rel="noopener">${row.file}</a></td>
					<td>${format_bytes(row.file_size)}</td>
					<td>${row.replicated ? 'Yes' : 'No'}</td>
					<td>${row.pending_delete ? 'Yes' : 'No'}</td>
					<td>${format_datetime(row.accessed_at)}</td>
				</tr>
			`
		)
		.join('');
	return `
		<div class="cache-status-section">
			<h4>Recent files (last 20 by access)</h4>
			<table class="table table-bordered">
				<thead>
					<tr><th>File</th><th>Size</th><th>Replicated</th><th>Pending delete</th><th>Last accessed</th></tr>
				</thead>
				<tbody>${rows}</tbody>
			</table>
		</div>
	`;
}
