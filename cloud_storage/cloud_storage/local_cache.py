# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import os

import frappe
from frappe.core.doctype.file.file import File
from frappe.utils import add_to_date, get_datetime


def get_local_cache_path(content_hash: str) -> str:
	"""Content-addressed path under sites/{site}/local_cache/."""
	directory = frappe.get_site_path("local_cache", content_hash[:2])
	os.makedirs(directory, exist_ok=True)
	return os.path.join(directory, content_hash)


def is_local_cache_enabled() -> bool:
	config = frappe.conf.cloud_storage_settings or {}
	return bool(config.get("local_cache_enabled"))


def is_warm_on_read_enabled() -> bool:
	config = frappe.conf.cloud_storage_settings or {}
	return bool(config.get("warm_on_read", True))


def is_cloud_storage_degraded() -> bool:
	return frappe.db.get_single_value("Cloud Storage Health", "status") == "Degraded"


def get_max_cache_size_bytes() -> int:
	config = frappe.conf.cloud_storage_settings or {}
	return int(config.get("max_cache_size_gb", 50) * 1024**3)


def get_emergency_cache_size_bytes() -> int:
	config = frappe.conf.cloud_storage_settings or {}
	return int(config.get("emergency_cache_size_gb", 80) * 1024**3)


def get_live_cache_record(filters: dict):
	"""The live (not evicted) Local File Cache doc matching filters, or None."""
	name = frappe.db.exists("Local File Cache", {**filters, "evicted": 0})
	return frappe.get_doc("Local File Cache", name) if name else None


def read_cache_bytes(cache) -> bytes | None:
	if not cache.local_path or not os.path.exists(cache.local_path):
		return None
	with open(cache.local_path, "rb") as fh:
		return fh.read()


def touch_cache_access(cache_name: str) -> None:
	frappe.db.set_value(
		"Local File Cache", cache_name, "accessed_at", get_datetime(), update_modified=False
	)


def enforce_local_read_permission(file_doc: File) -> None:
	if file_doc.is_private:
		frappe.has_permission(
			doctype="File", ptype="read", doc=file_doc, user=frappe.session.user, throw=True
		)


def get_cached_content(file: File) -> bytes | None:
	"""Bytes for `file` from the local cache, enforcing read permission; None on a miss."""
	if not is_local_cache_enabled():
		return None
	cache = get_live_cache_record({"file": file.name})
	if not cache:
		return None
	content = read_cache_bytes(cache)
	if content is None:
		return None
	enforce_local_read_permission(file)
	touch_cache_access(cache.name)
	return content


def warm_local_cache(file: File, content: bytes) -> None:
	"""Persist bytes fetched from object storage into the local cache (warm_on_read).

	Re-admits a previously evicted row instead of skipping it - `file` is unique,
	so an evicted row must be reused rather than left to block future inserts.
	"""
	if not is_local_cache_enabled() or not is_warm_on_read_enabled():
		return
	if len(content) > get_max_cache_size_bytes() * 0.05:
		return

	existing_name = frappe.db.exists("Local File Cache", {"file": file.name})
	if existing_name and not frappe.db.get_value("Local File Cache", existing_name, "evicted"):
		return

	local_path = get_local_cache_path(file.content_hash)
	with open(local_path, "wb") as fh:
		fh.write(content)

	fields = {
		"local_path": local_path,
		"file_size": len(content),
		"s3_key": file.s3_key,
		"content_hash": file.content_hash,
		"replicated": 1,
		"replicated_at": get_datetime(),
		"accessed_at": get_datetime(),
		"evicted": 0,
		"evicted_at": None,
	}

	if existing_name:
		cache = frappe.get_doc("Local File Cache", existing_name)
		cache.update(fields)
		cache.save(ignore_permissions=True)
	else:
		frappe.get_doc({"doctype": "Local File Cache", "file": file.name, **fields}).insert(
			ignore_permissions=True
		)


def get_cached_bytes_total() -> int:
	return int(frappe.qb.sum("Local File Cache", "file_size", filters={"evicted": 0}))


def get_unevictable_bytes_total() -> int:
	return int(frappe.qb.sum("Local File Cache", "file_size", filters={"evicted": 0, "replicated": 0}))


def is_emergency_ceiling_unrecoverable(incoming_bytes: int = 0) -> bool:
	"""True when evicting every replicated row still can't clear the emergency ceiling
	once `incoming_bytes` (the upload being admitted) is accounted for."""
	return get_unevictable_bytes_total() + incoming_bytes > get_emergency_cache_size_bytes()


def evict_candidates(filters: dict, target_bytes: int, current_total: int) -> int:
	"""Evict oldest-accessed matches until current_total <= target_bytes. Returns the new total."""
	candidates = frappe.get_all(
		"Local File Cache",
		filters=filters,
		fields=["name", "file_size", "local_path"],
		order_by="accessed_at asc",
	)
	for candidate in candidates:
		if current_total <= target_bytes:
			break
		if candidate.local_path and os.path.exists(candidate.local_path):
			os.remove(candidate.local_path)
		frappe.db.set_value(
			"Local File Cache",
			candidate.name,
			{"evicted": 1, "evicted_at": get_datetime()},
			update_modified=False,
		)
		current_total -= candidate.file_size
	return current_total


def write_local_cache_bytes(file: File) -> str:
	"""Write `file`'s bytes to its content-addressed cache path. Returns the path."""
	if file.name:
		# overwrite of an already-cached file under a new hash: drop the stale bytes now
		existing_name = frappe.db.exists("Local File Cache", {"file": file.name})
		if existing_name:
			old_hash = frappe.db.get_value("Local File Cache", existing_name, "content_hash")
			if old_hash and old_hash != file.content_hash:
				old_path = get_local_cache_path(old_hash)
				if os.path.exists(old_path):
					os.remove(old_path)

	local_path = get_local_cache_path(file.content_hash)
	with open(local_path, "wb") as fh:
		fh.write(file.content)
	return local_path


def admit_local_cache_record(file: File, local_path: str) -> None:
	"""Create/update `file`'s Local File Cache row with replicated=0. Requires file.name."""
	fields = {
		"local_path": local_path,
		"file_size": len(file.content),
		"s3_key": file.s3_key,
		"content_hash": file.content_hash,
		"replicated": 0,
		"replicated_at": None,
		"replication_attempts": 0,
		"last_replication_error": None,
		"accessed_at": get_datetime(),
		"evicted": 0,
		"evicted_at": None,
	}

	existing_name = frappe.db.exists("Local File Cache", {"file": file.name})
	try:
		if existing_name:
			cache = frappe.get_doc("Local File Cache", existing_name)
			cache.update(fields)
			cache.save(ignore_permissions=True)
		else:
			frappe.get_doc({"doctype": "Local File Cache", "file": file.name, **fields}).insert(
				ignore_permissions=True
			)
	except Exception:
		if os.path.exists(local_path):
			os.remove(local_path)
		raise


def enqueue_replication(local_file_cache_name: str) -> None:
	frappe.enqueue(
		"cloud_storage.cloud_storage.tasks.replicate_cached_file",
		local_file_cache_name=local_file_cache_name,
		queue="short",
		enqueue_after_commit=True,
	)


def delete_cache_record(file_name: str) -> None:
	cache_name = frappe.db.exists("Local File Cache", {"file": file_name})
	if not cache_name:
		return
	local_path = frappe.db.get_value("Local File Cache", cache_name, "local_path")
	if local_path and os.path.exists(local_path):
		os.remove(local_path)
	frappe.delete_doc("Local File Cache", cache_name, ignore_permissions=True)


def tombstone_cache_record(file: File) -> None:
	"""Remove local bytes and mark pending_delete=1, keeping s3_key for process_pending_deletes.
	Admits a row on the fly for files that were never cached, so the remote object isn't orphaned."""
	cache_name = frappe.db.exists("Local File Cache", {"file": file.name})
	if cache_name:
		local_path = frappe.db.get_value("Local File Cache", cache_name, "local_path")
		if local_path and os.path.exists(local_path):
			os.remove(local_path)
		frappe.db.set_value("Local File Cache", cache_name, "pending_delete", 1, update_modified=False)
	else:
		frappe.get_doc(
			{"doctype": "Local File Cache", "file": file.name, "s3_key": file.s3_key, "pending_delete": 1}
		).insert(ignore_permissions=True)


def get_retention_cutoff():
	config = frappe.conf.cloud_storage_settings or {}
	return add_to_date(get_datetime(), minutes=-config.get("cache_retention_minutes", 60))
