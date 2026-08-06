# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt
# Write buffers used by wsgidav resources.

import io
import mimetypes
from typing import Any

import frappe
from frappe.core.doctype.file.utils import get_content_hash

from cloud_storage.cloud_storage.webdav import memory as os_file_store
from cloud_storage.cloud_storage.webdav import paths


class WriteBuffer(io.RawIOBase):
	"""Accumulates PUT body; on close inserts a File doc and uploads content.

	Finder's two-step upload sends PUT Content-Length:0 first (to claim the
	path), then LOCK, then a second PUT with real content. An empty body
	creates a placeholder doc so the LOCK handler can find the resource.
	"""

	def __init__(self, file_name: str, frappe_folder: str, content_type: str | None) -> None:
		self._buf = io.BytesIO()
		self._file_name = file_name
		self._frappe_folder = frappe_folder
		self._content_type = content_type

	def write(self, data: Any) -> int:
		return self._buf.write(data)

	def close(self) -> None:
		if not self.closed:
			super().close()
			try:
				self.commit()
			except Exception:
				frappe.log_error("WebDAV upload error", frappe.get_traceback())
				raise

	def commit(self) -> None:
		content = self._buf.getvalue()

		file_doc = frappe.new_doc("File")
		file_doc.file_name = self._file_name
		file_doc.folder = self._frappe_folder
		# Private by default: non-private files skip expiration + perm checks in
		# get_presigned_url, undermining the per-user WebDAV perm model.
		file_doc.is_private = 1
		file_doc.flags.cloud_storage = True

		if not content:
			file_doc.insert()
			frappe.db.commit()
			return

		file_doc.content = content
		file_doc.content_hash = get_content_hash(content)
		mime, _ = mimetypes.guess_type(self._file_name or "")
		file_doc.content_type = mime or "application/octet-stream"
		file_doc.insert()

		config = frappe.conf.get("cloud_storage_settings", {})
		if not config or config.get("use_local"):
			# save_file_on_filesystem reads _content, not content.
			file_doc._content = content
			file_doc.save_file_on_filesystem()
			file_doc.db_set("file_url", file_doc.file_url)
			file_doc.db_set("file_size", len(content))
		else:
			paths.upload_via_webdav(file_doc, content, file_doc.content_type)
		frappe.db.commit()


class OverwriteBuffer(io.RawIOBase):
	"""Accumulates PUT body for an existing resource; on close re-uploads content in place."""

	def __init__(self, doc_name: str) -> None:
		self._buf = io.BytesIO()
		self._doc_name = doc_name

	def write(self, data: Any) -> int:
		return self._buf.write(data)

	def close(self) -> None:
		if not self.closed:
			super().close()
			try:
				self.commit()
			except Exception:
				frappe.log_error("WebDAV overwrite error", frappe.get_traceback())
				raise

	def commit(self) -> None:
		content = self._buf.getvalue()
		if not content:
			return

		content_hash = get_content_hash(content)
		file_doc = frappe.get_doc("File", self._doc_name)
		mime, _ = mimetypes.guess_type(file_doc.file_name or "")
		content_type = mime or "application/octet-stream"
		file_doc.content_hash = content_hash
		file_doc.content_type = content_type
		file_doc.flags.cloud_storage = True

		config = frappe.conf.get("cloud_storage_settings", {})
		if not config or config.get("use_local"):
			file_doc.content = content
			file_doc.file_size = len(content)
			# Clear the existing file_url (likely an S3 retrieve URL) so
			# save_file_on_filesystem's validate doesn't reject it.
			# Also assign _content — that's what the method actually reads.
			file_doc.file_url = None
			file_doc._content = content
			file_doc.save_file_on_filesystem()
			file_doc.db_set("file_url", file_doc.file_url)
			file_doc.db_set("file_size", len(content))
			file_doc.db_set("content_hash", content_hash)
		else:
			paths.upload_via_webdav(file_doc, content, content_type)
		frappe.db.commit()


class MemoryBuffer(io.RawIOBase):
	"""Write buffer that stashes content in the Redis-backed OS metadata store."""

	def __init__(self, path: str) -> None:
		self._buf = io.BytesIO()
		self._path = path

	def write(self, data: Any) -> int:
		return self._buf.write(data)

	def close(self) -> None:
		if not self.closed:
			super().close()
			os_file_store.set(self._path, self._buf.getvalue())
