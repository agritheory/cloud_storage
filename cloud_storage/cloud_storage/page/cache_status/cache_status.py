import frappe

from cloud_storage.cloud_storage.local_cache import (
	FIELDS,
	get_cached_bytes_total,
	get_connection,
	get_emergency_cache_size_bytes,
	get_max_cache_size_bytes,
	get_unevictable_bytes_total,
	is_local_cache_enabled,
	row_to_record,
)


def config_summary(config: dict) -> dict:
	return {
		"local_cache_enabled": bool(config.get("local_cache_enabled")),
		"max_cache_size_gb": config.get("max_cache_size_gb", 50),
		"emergency_cache_size_gb": config.get("emergency_cache_size_gb", 80),
		"cache_retention_minutes": config.get("cache_retention_minutes", 60),
		"warm_on_read": config.get("warm_on_read", True),
		"replication_max_retries": config.get("replication_max_retries", 10),
	}


def health_summary(health: dict) -> dict:
	return {
		"status": health.get("status"),
		"consecutive_failures": health.get("consecutive_failures"),
		"failure_threshold": health.get("failure_threshold"),
		"degraded_since": str(health.get("degraded_since")) if health.get("degraded_since") else None,
		"last_error": health.get("last_error"),
		"last_check_at": str(health.get("last_check_at")) if health.get("last_check_at") else None,
	}


@frappe.whitelist()
def get_status():
	frappe.only_for("System Manager")

	config = frappe.conf.cloud_storage_settings or {}
	health = frappe.db.get_singles_dict("Cloud Storage Health")

	if not is_local_cache_enabled():
		return {
			"config": config_summary(config),
			"health": health_summary(health),
			"cache": None,
			"recent": [],
		}

	conn = get_connection()
	total_rows, live_rows, unreplicated_rows, pending_delete_rows = conn.execute(
		"SELECT "
		"COUNT(*), "
		"SUM(CASE WHEN evicted=0 AND pending_delete=0 THEN 1 ELSE 0 END), "
		"SUM(CASE WHEN replicated=0 AND evicted=0 AND pending_delete=0 THEN 1 ELSE 0 END), "
		"SUM(CASE WHEN pending_delete=1 THEN 1 ELSE 0 END) "
		"FROM local_file_cache"
	).fetchone()

	recent = conn.execute(
		f"SELECT {FIELDS} FROM local_file_cache WHERE evicted=0 ORDER BY accessed_at DESC LIMIT 20"
	).fetchall()

	return {
		"config": config_summary(config),
		"health": health_summary(health),
		"cache": {
			"total_rows": total_rows or 0,
			"live_rows": live_rows or 0,
			"unreplicated_rows": unreplicated_rows or 0,
			"pending_delete_rows": pending_delete_rows or 0,
			"cached_bytes_total": get_cached_bytes_total(),
			"unevictable_bytes_total": get_unevictable_bytes_total(),
			"max_cache_size_bytes": get_max_cache_size_bytes(),
			"emergency_cache_size_bytes": get_emergency_cache_size_bytes(),
		},
		"recent": [vars(row_to_record(row)) for row in recent],
	}
