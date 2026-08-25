# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import os
from pathlib import Path

import frappe
from magic import from_buffer

from cloud_storage.cloud_storage.local_cache import (
	FIELDS,
	evict_candidates,
	get_cached_bytes_total,
	get_connection,
	get_emergency_cache_size_bytes,
	get_failure_threshold,
	get_health,
	get_max_cache_size_bytes,
	get_retention_cutoff,
	iso,
	is_cloud_storage_degraded,
	is_local_cache_enabled,
	read_cache_bytes,
	row_to_record,
	update_health,
)
from cloud_storage.cloud_storage.overrides.file import get_cloud_storage_client


def mark_replicated_if_current(local_file_cache_name, content_hash: str, s3_key: str, now) -> bool:
	conn = get_connection()
	conn.execute("BEGIN IMMEDIATE")
	cursor = conn.execute(
		"UPDATE local_file_cache SET replicated=1, replicated_at=?, last_replication_error=NULL "
		"WHERE id=? AND content_hash=? AND s3_key=? AND replicated=0 AND pending_delete=0",
		(iso(now), local_file_cache_name, content_hash, s3_key),
	)
	applied = cursor.rowcount > 0
	conn.execute("COMMIT")
	return applied


def replicate_cached_file(local_file_cache_name):
	conn = get_connection()
	cursor = conn.execute(
		f"SELECT {FIELDS} FROM local_file_cache WHERE id = ?", (local_file_cache_name,)
	)
	cache = row_to_record(cursor.fetchone())
	if not cache:
		return
	if cache.replicated:
		return
	if not frappe.db.exists("File", cache.file):
		return

	content = read_cache_bytes(cache)
	if content is None:
		frappe.log_error(
			f"Local cache bytes missing for {local_file_cache_name}", "Cloud Storage Replication Error"
		)
		return

	file = frappe.get_doc("File", cache.file)
	client = get_cloud_storage_client()
	content_type = getattr(file, "content_type", None) or from_buffer(content, mime=True)

	try:
		response = client.put_object(
			Body=content, Bucket=client.bucket, Key=cache.s3_key, ContentType=content_type
		)
	except Exception as e:
		conn.execute(
			"UPDATE local_file_cache SET replication_attempts=replication_attempts+1, "
			"last_replication_error=? WHERE id=?",
			(str(e), local_file_cache_name),
		)
		return

	s3_version_id = response.get("VersionId")
	applied = mark_replicated_if_current(
		local_file_cache_name, cache.content_hash, cache.s3_key, frappe.utils.now_datetime()
	)
	if not applied or not frappe.db.exists("File", cache.file):
		if s3_version_id:
			try:
				client.delete_object(Bucket=client.bucket, Key=cache.s3_key, VersionId=s3_version_id)
			except Exception as e:
				frappe.log_error(str(e), "Cloud Storage Error: Could not delete orphaned replicated object")
		else:
			frappe.log_error(
				f"Orphaned replicated object without a VersionId, left in place: {cache.s3_key}",
				"Cloud Storage Replication Error",
			)
		return

	version_id = s3_version_id or file.content_hash
	file.add_file_version(version_id)


def evict_lru_cache():
	if is_cloud_storage_degraded():
		return

	total = get_cached_bytes_total()

	max_bytes = get_max_cache_size_bytes()
	if total > max_bytes:
		total = evict_candidates(
			{"evicted": 0, "replicated": 1, "accessed_at": ["<", get_retention_cutoff()]}, max_bytes, total
		)

	emergency_bytes = get_emergency_cache_size_bytes()
	if total > emergency_bytes:
		evict_candidates({"evicted": 0, "replicated": 1}, emergency_bytes, total)


def check_cloud_health():
	if not is_local_cache_enabled():
		return

	health = get_health()
	now = frappe.utils.now_datetime()
	client = get_cloud_storage_client()

	try:
		client.head_bucket(Bucket=client.bucket)
	except Exception as e:
		consecutive_failures = (health.consecutive_failures or 0) + 1
		updates = {
			"consecutive_failures": consecutive_failures,
			"last_error": str(e),
			"last_check_at": now,
		}
		if consecutive_failures >= get_failure_threshold() and health.status != "Degraded":
			updates["status"] = "Degraded"
			updates["degraded_since"] = now
		update_health(updates)
		return

	updates = {"consecutive_failures": 0, "last_error": None, "last_check_at": now}
	if health.status == "Degraded":
		updates["status"] = "Healthy"
		updates["degraded_since"] = None
		# outage over: attempts racked up during it shouldn't count against replication_max_retries
		get_connection().execute(
			"UPDATE local_file_cache SET replication_attempts=0, last_replication_error=NULL "
			"WHERE replicated=0 AND pending_delete=0"
		)
		update_health(updates)
		retry_pending_replications()
		return
	update_health(updates)


def retry_pending_replications():
	if not is_local_cache_enabled():
		return

	max_retries = (frappe.conf.cloud_storage_settings or {}).get("replication_max_retries", 10)
	cursor = get_connection().execute(
		"SELECT id FROM local_file_cache WHERE replicated=0 AND pending_delete=0 "
		"AND replication_attempts < ? ORDER BY creation ASC",
		(max_retries,),
	)
	for (cache_id,) in cursor.fetchall():
		replicate_cached_file(cache_id)


def process_pending_deletes():
	if not is_local_cache_enabled():
		return

	conn = get_connection()
	pending = conn.execute(
		"SELECT id, s3_key FROM local_file_cache WHERE pending_delete=1 ORDER BY creation ASC"
	).fetchall()
	if not pending:
		return

	client = get_cloud_storage_client()
	for cache_id, s3_key in pending:
		try:
			client.delete_object(Bucket=client.bucket, Key=s3_key)
		except Exception as e:
			frappe.log_error(str(e), "Cloud Storage Error: Could not delete tombstoned file")
			continue
		conn.execute("DELETE FROM local_file_cache WHERE id=?", (cache_id,))


def reconcile_local_cache():
	if not is_local_cache_enabled():
		return

	conn = get_connection()
	live_rows = conn.execute(
		"SELECT id, local_path FROM local_file_cache WHERE evicted=0 AND pending_delete=0"
	).fetchall()
	known_paths = set()
	for cache_id, local_path in live_rows:
		if local_path and os.path.exists(local_path):
			known_paths.add(Path(local_path))
		else:
			conn.execute(
				"UPDATE local_file_cache SET evicted=1, evicted_at=? WHERE id=?",
				(iso(frappe.utils.now_datetime()), cache_id),
			)

	cache_root = Path(frappe.get_site_path("local_cache"))
	if not cache_root.is_dir():
		return
	index_db_names = {"index.db", "index.db-wal", "index.db-shm", "index.db-journal"}
	for path in cache_root.rglob("*"):
		if path.name in index_db_names:
			continue
		if path.is_file() and path not in known_paths:
			path.unlink()
