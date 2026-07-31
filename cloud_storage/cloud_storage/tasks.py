# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import os
from pathlib import Path

import frappe
from frappe.query_builder import DocType
from magic import from_buffer

from cloud_storage.cloud_storage.local_cache import (
	evict_candidates,
	get_cached_bytes_total,
	get_emergency_cache_size_bytes,
	get_max_cache_size_bytes,
	get_retention_cutoff,
	is_cloud_storage_degraded,
	is_local_cache_enabled,
	read_cache_bytes,
)
from cloud_storage.cloud_storage.overrides.file import get_cloud_storage_client


def mark_replicated_if_current(local_file_cache_name: str, content_hash: str, s3_key: str, now) -> bool:
	LocalFileCache = DocType("Local File Cache")
	(
		frappe.qb.update(LocalFileCache)
		.set(LocalFileCache.replicated, 1)
		.set(LocalFileCache.replicated_at, now)
		.set(LocalFileCache.last_replication_error, None)
		.where(LocalFileCache.name == local_file_cache_name)
		.where(LocalFileCache.content_hash == content_hash)
		.where(LocalFileCache.s3_key == s3_key)
		.where(LocalFileCache.replicated == 0)
		.where(LocalFileCache.pending_delete == 0)
	).run()
	return frappe.db._cursor.rowcount > 0


def replicate_cached_file(local_file_cache_name: str):
	if not frappe.db.exists("Local File Cache", local_file_cache_name):
		# Row (and its File) were deleted between enqueue and this job running.
		return

	cache = frappe.get_doc("Local File Cache", local_file_cache_name)
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
		cache.db_set("replication_attempts", (cache.replication_attempts or 0) + 1)
		cache.db_set("last_replication_error", str(e))
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
				frappe.log_error(
					str(e), "Cloud Storage Error: Could not delete orphaned replicated object"
				)
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

	health = frappe.get_single("Cloud Storage Health")
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
		if consecutive_failures >= (health.failure_threshold or 3) and health.status != "Degraded":
			updates["status"] = "Degraded"
			updates["degraded_since"] = now
		frappe.db.set_single_value("Cloud Storage Health", updates, update_modified=False)
		return

	updates = {"consecutive_failures": 0, "last_error": None, "last_check_at": now}
	if health.status == "Degraded":
		updates["status"] = "Healthy"
		updates["degraded_since"] = None
		# outage over: attempts racked up during it shouldn't count against replication_max_retries
		frappe.db.set_value(
			"Local File Cache",
			{"replicated": 0, "pending_delete": 0},
			{"replication_attempts": 0, "last_replication_error": None},
			update_modified=False,
		)
		frappe.db.set_single_value("Cloud Storage Health", updates, update_modified=False)
		retry_pending_replications()
		return
	frappe.db.set_single_value("Cloud Storage Health", updates, update_modified=False)


def retry_pending_replications():
	if not is_local_cache_enabled():
		return

	max_retries = (frappe.conf.cloud_storage_settings or {}).get("replication_max_retries", 10)
	pending = frappe.get_all(
		"Local File Cache",
		filters={"replicated": 0, "pending_delete": 0, "replication_attempts": ["<", max_retries]},
		fields=["name"],
		order_by="creation asc",
	)
	for row in pending:
		replicate_cached_file(row.name)


def process_pending_deletes():
	if not is_local_cache_enabled():
		return

	pending = frappe.get_all(
		"Local File Cache",
		filters={"pending_delete": 1},
		fields=["name", "s3_key"],
		order_by="creation asc",
	)
	if not pending:
		return

	client = get_cloud_storage_client()
	for row in pending:
		try:
			client.delete_object(Bucket=client.bucket, Key=row.s3_key)
		except Exception as e:
			frappe.log_error(str(e), "Cloud Storage Error: Could not delete tombstoned file")
			continue
		frappe.delete_doc("Local File Cache", row.name, ignore_permissions=True)


def reconcile_local_cache():
	if not is_local_cache_enabled():
		return

	live_rows = frappe.get_all(
		"Local File Cache", filters={"evicted": 0, "pending_delete": 0}, fields=["name", "local_path"]
	)
	known_paths = set()
	for row in live_rows:
		if row.local_path and os.path.exists(row.local_path):
			known_paths.add(Path(row.local_path))
		else:
			frappe.db.set_value(
				"Local File Cache",
				row.name,
				{"evicted": 1, "evicted_at": frappe.utils.now_datetime()},
				update_modified=False,
			)

	cache_root = Path(frappe.get_site_path("local_cache"))
	if not cache_root.is_dir():
		return
	for path in cache_root.rglob("*"):
		if path.is_file() and path not in known_paths:
			path.unlink()
