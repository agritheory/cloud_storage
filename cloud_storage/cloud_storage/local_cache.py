# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import os

import frappe
from frappe.core.doctype.file.file import File
from frappe.utils import get_datetime


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
