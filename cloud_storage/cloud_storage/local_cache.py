# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import os
import sqlite3
from types import SimpleNamespace

import frappe
from frappe.core.doctype.file.file import File
from frappe.utils import add_to_date, get_datetime

FIELDS = (
	"id, file, local_path, file_size, s3_key, content_hash, replicated, replicated_at, "
	"replication_attempts, last_replication_error, accessed_at, evicted, evicted_at, "
	"pending_delete, creation"
)


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
	return is_local_cache_enabled() and get_health().status == "Degraded"


def get_failure_threshold() -> int:
	config = frappe.conf.cloud_storage_settings or {}
	return int(config.get("failure_threshold", 3))


def get_max_cache_size_bytes() -> int:
	config = frappe.conf.cloud_storage_settings or {}
	return int(config.get("max_cache_size_gb", 50) * 1024**3)


def get_emergency_cache_size_bytes() -> int:
	config = frappe.conf.cloud_storage_settings or {}
	return int(config.get("emergency_cache_size_gb", 80) * 1024**3)


def iso(dt) -> str:
	return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


def build_where(filters: dict) -> tuple[str, tuple]:
	clauses = []
	params = []
	for key, value in filters.items():
		if isinstance(value, (list, tuple)):
			operator, operand = value
			clauses.append(f"{key} {operator} ?")
			params.append(iso(operand) if hasattr(operand, "strftime") else operand)
		else:
			clauses.append(f"{key} = ?")
			params.append(value)
	return (" AND ".join(clauses) if clauses else "1=1"), tuple(params)


def get_index_db_path() -> str:
	return frappe.get_site_path("local_cache", "index.db")


def get_connection() -> sqlite3.Connection:
	path = get_index_db_path()
	os.makedirs(os.path.dirname(path), exist_ok=True)
	conn = sqlite3.connect(path, timeout=30)
	conn.isolation_level = None
	conn.execute("PRAGMA journal_mode=WAL")
	conn.execute("PRAGMA busy_timeout=30000")
	conn.execute(
		"""
		CREATE TABLE IF NOT EXISTS local_file_cache (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			file TEXT UNIQUE NOT NULL,
			local_path TEXT,
			file_size INTEGER,
			s3_key TEXT,
			content_hash TEXT,
			replicated INTEGER DEFAULT 0,
			replicated_at TEXT,
			replication_attempts INTEGER DEFAULT 0,
			last_replication_error TEXT,
			accessed_at TEXT,
			evicted INTEGER DEFAULT 0,
			evicted_at TEXT,
			pending_delete INTEGER DEFAULT 0,
			creation TEXT
		)
		"""
	)
	conn.execute(
		"CREATE INDEX IF NOT EXISTS idx_local_file_cache_s3_key ON local_file_cache (s3_key)"
	)
	conn.execute(
		"""
		CREATE TABLE IF NOT EXISTS cloud_storage_health (
			id INTEGER PRIMARY KEY CHECK (id = 1),
			status TEXT DEFAULT 'Healthy',
			consecutive_failures INTEGER DEFAULT 0,
			degraded_since TEXT,
			last_check_at TEXT,
			last_error TEXT
		)
		"""
	)
	conn.execute(
		"INSERT OR IGNORE INTO cloud_storage_health (id, status, consecutive_failures) VALUES (1, 'Healthy', 0)"
	)
	return conn


def get_health():
	row = (
		get_connection()
		.execute(
			"SELECT status, consecutive_failures, degraded_since, last_check_at, last_error "
			"FROM cloud_storage_health WHERE id = 1"
		)
		.fetchone()
	)
	return SimpleNamespace(
		status=row[0],
		consecutive_failures=row[1],
		degraded_since=row[2],
		last_check_at=row[3],
		last_error=row[4],
	)


def update_health(updates: dict) -> None:
	set_clause = ", ".join(f"{key} = ?" for key in updates)
	params = [iso(value) if hasattr(value, "strftime") else value for value in updates.values()]
	get_connection().execute(f"UPDATE cloud_storage_health SET {set_clause} WHERE id = 1", params)


def row_to_record(row):
	if row is None:
		return None
	return SimpleNamespace(
		id=row[0],
		name=row[0],
		file=row[1],
		local_path=row[2],
		file_size=row[3],
		s3_key=row[4],
		content_hash=row[5],
		replicated=row[6],
		replicated_at=row[7],
		replication_attempts=row[8],
		last_replication_error=row[9],
		accessed_at=row[10],
		evicted=row[11],
		evicted_at=row[12],
		pending_delete=row[13],
		creation=row[14],
	)


def get_live_cache_record(filters: dict):
	"""The live (not evicted) Local File Cache row matching filters, or None."""
	where, params = build_where(filters)
	cursor = get_connection().execute(
		f"SELECT {FIELDS} FROM local_file_cache WHERE {where} AND evicted = 0", params
	)
	return row_to_record(cursor.fetchone())


def read_cache_bytes(cache) -> bytes | None:
	if not cache.local_path or not os.path.exists(cache.local_path):
		return None
	with open(cache.local_path, "rb") as fh:
		return fh.read()


def touch_cache_access(cache_name) -> None:
	get_connection().execute(
		"UPDATE local_file_cache SET accessed_at = ? WHERE id = ?", (iso(get_datetime()), cache_name)
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

	conn = get_connection()
	cursor = conn.execute("SELECT id, evicted FROM local_file_cache WHERE file = ?", (file.name,))
	existing = cursor.fetchone()
	if existing and not existing[1]:
		return

	local_path = get_local_cache_path(file.content_hash)
	with open(local_path, "wb") as fh:
		fh.write(content)

	now = iso(get_datetime())
	if existing:
		conn.execute(
			"UPDATE local_file_cache SET local_path=?, file_size=?, s3_key=?, content_hash=?, "
			"replicated=1, replicated_at=?, accessed_at=?, evicted=0, evicted_at=NULL WHERE id=?",
			(local_path, len(content), file.s3_key, file.content_hash, now, now, existing[0]),
		)
	else:
		conn.execute(
			"INSERT INTO local_file_cache "
			"(file, local_path, file_size, s3_key, content_hash, replicated, replicated_at, accessed_at, creation) "
			"VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
			(file.name, local_path, len(content), file.s3_key, file.content_hash, now, now, now),
		)


def get_cached_bytes_total() -> int:
	cursor = get_connection().execute(
		"SELECT COALESCE(SUM(file_size), 0) FROM local_file_cache WHERE evicted = 0 AND pending_delete = 0"
	)
	return cursor.fetchone()[0]


def get_unevictable_bytes_total() -> int:
	cursor = get_connection().execute(
		"SELECT COALESCE(SUM(file_size), 0) FROM local_file_cache "
		"WHERE evicted = 0 AND replicated = 0 AND pending_delete = 0"
	)
	return cursor.fetchone()[0]


def is_emergency_ceiling_unrecoverable(incoming_bytes: int = 0) -> bool:
	"""True when evicting every replicated row still can't clear the emergency ceiling
	once `incoming_bytes` (the upload being admitted) is accounted for."""
	return get_unevictable_bytes_total() + incoming_bytes > get_emergency_cache_size_bytes()


def evict_candidates(filters: dict, target_bytes: int, current_total: int) -> int:
	"""Evict oldest-accessed matches until current_total <= target_bytes. Returns the new total."""
	conn = get_connection()
	where, params = build_where({**filters, "pending_delete": 0})
	cursor = conn.execute(
		f"SELECT id, file_size, local_path FROM local_file_cache WHERE {where} ORDER BY accessed_at ASC",
		params,
	)
	for cache_id, file_size, local_path in cursor.fetchall():
		if current_total <= target_bytes:
			break
		if local_path and os.path.exists(local_path):
			os.remove(local_path)
		conn.execute(
			"UPDATE local_file_cache SET evicted=1, evicted_at=? WHERE id=?",
			(iso(get_datetime()), cache_id),
		)
		current_total -= file_size
	return current_total


def write_local_cache_bytes(file: File) -> str:
	local_path = get_local_cache_path(file.content_hash)
	with open(local_path, "wb") as fh:
		fh.write(file.content)
	return local_path


def admit_local_cache_record(file: File, local_path: str) -> str | None:
	conn = get_connection()
	cursor = conn.execute("SELECT id, local_path FROM local_file_cache WHERE file = ?", (file.name,))
	existing = cursor.fetchone()
	now = iso(get_datetime())
	previous_local_path = existing[1] if existing else None

	try:
		if existing:
			conn.execute(
				"UPDATE local_file_cache SET local_path=?, file_size=?, s3_key=?, content_hash=?, "
				"replicated=0, replicated_at=NULL, replication_attempts=0, last_replication_error=NULL, "
				"accessed_at=?, evicted=0, evicted_at=NULL WHERE id=?",
				(local_path, len(file.content), file.s3_key, file.content_hash, now, existing[0]),
			)
		else:
			conn.execute(
				"INSERT INTO local_file_cache "
				"(file, local_path, file_size, s3_key, content_hash, accessed_at, creation) "
				"VALUES (?, ?, ?, ?, ?, ?, ?)",
				(file.name, local_path, len(file.content), file.s3_key, file.content_hash, now, now),
			)
	except Exception:
		if os.path.exists(local_path):
			os.remove(local_path)
		raise

	if previous_local_path == local_path:
		return None
	return previous_local_path


def enqueue_replication(local_file_cache_name) -> None:
	frappe.enqueue(
		"cloud_storage.cloud_storage.tasks.replicate_cached_file",
		local_file_cache_name=local_file_cache_name,
		queue="short",
		enqueue_after_commit=True,
	)


def delete_cache_record(file_name: str) -> None:
	conn = get_connection()
	cursor = conn.execute("SELECT local_path FROM local_file_cache WHERE file = ?", (file_name,))
	row = cursor.fetchone()
	if not row:
		return
	local_path = row[0]
	if local_path and os.path.exists(local_path):
		os.remove(local_path)
	conn.execute("DELETE FROM local_file_cache WHERE file = ?", (file_name,))


def tombstone_cache_record(file: File) -> None:
	"""Remove local bytes and mark pending_delete=1, keeping s3_key for process_pending_deletes.
	Admits a row on the fly for files that were never cached, so the remote object isn't orphaned."""
	conn = get_connection()
	cursor = conn.execute("SELECT id, local_path FROM local_file_cache WHERE file = ?", (file.name,))
	row = cursor.fetchone()
	if row:
		cache_id, local_path = row
		if local_path and os.path.exists(local_path):
			os.remove(local_path)
		conn.execute("UPDATE local_file_cache SET pending_delete=1 WHERE id=?", (cache_id,))
	else:
		conn.execute(
			"INSERT INTO local_file_cache (file, s3_key, pending_delete, creation) VALUES (?, ?, 1, ?)",
			(file.name, file.s3_key, iso(get_datetime())),
		)


def get_retention_cutoff():
	config = frappe.conf.cloud_storage_settings or {}
	return add_to_date(get_datetime(), minutes=-config.get("cache_retention_minutes", 60))
