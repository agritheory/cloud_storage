# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from magic import from_buffer

from cloud_storage.cloud_storage.local_cache import (
	evict_candidates,
	get_cached_bytes_total,
	get_emergency_cache_size_bytes,
	get_max_cache_size_bytes,
	get_retention_cutoff,
	read_cache_bytes,
)
from cloud_storage.cloud_storage.overrides.file import get_cloud_storage_client


def replicate_cached_file(local_file_cache_name: str):
	if not frappe.db.exists("Local File Cache", local_file_cache_name):
		# Row (and its File) were deleted between enqueue and this job running.
		return

	cache = frappe.get_doc("Local File Cache", local_file_cache_name)
	if cache.replicated:
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

	version_id = response.get("VersionId") or file.content_hash
	file.add_file_version(version_id)

	cache.db_set("replicated", 1)
	cache.db_set("replicated_at", frappe.utils.now_datetime())


def evict_lru_cache():
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
	pass


def process_pending_deletes():
	pass
