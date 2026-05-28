# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

from __future__ import annotations

import io
import mimetypes
from typing import Optional

import frappe
from wsgidav.dav_error import (
	DAVError,
	HTTP_FORBIDDEN,
	HTTP_METHOD_NOT_ALLOWED,
	HTTP_NOT_FOUND,
)
from wsgidav.dav_provider import DAVCollection, DAVNonCollection, DAVProvider


def _dav_path_to_frappe_folder(dav_path: str) -> str:
	"""Map a WebDAV path to the corresponding Frappe folder name.

	/              → Home
	/Attachments/  → Home/Attachments
	/a/b/c/        → Home/a/b/c
	"""
	parts = [p for p in dav_path.strip("/").split("/") if p]
	if not parts:
		return "Home"
	return "Home/" + "/".join(parts)


def _folder_display_name(frappe_folder: str) -> str:
	return frappe_folder.rsplit("/", 1)[-1]


_SYSTEM_FOLDERS = {"Home", "Home/Attachments"}
_OS_FILES = frozenset({".DS_Store", "desktop.ini", "Thumbs.db", ".Trashes", ".Spotlight-V100"})
_OS_PREFIXES = ("._",)

# Editor / OS atomic-save scratchpads. macOS TextEdit and many other editors
# write via MKCOL → PUT → MOVE → DELETE on a sibling folder named like
# "<file>.sb-<hex>". They're real folders/files (we must let MKCOL/PUT/MOVE
# work on them) but they only ever exist for a few hundred ms — we hide them
# from listings so they don't appear in Finder if a save crashes mid-flight.
_TEMP_PREFIXES = (".sb-",)

# In-memory store for OS metadata files (AppleDouble ._*, .DS_Store, ...).
# Finder requires these to read/write/lock successfully for drag-and-drop to work,
# but they're transient and shouldn't pollute Frappe / S3. They live here only
# for the lifetime of the server process — small, ephemeral, and per-server.
_OS_FILE_STORE: dict[str, bytes] = {}


def _is_os_metadata(name: str) -> bool:
	return name in _OS_FILES or name.startswith(_OS_PREFIXES)


def _is_hidden_from_listing(name: str) -> bool:
	return name.startswith(_OS_PREFIXES) or name in _OS_FILES or name.startswith(_TEMP_PREFIXES)


def _parse_dest(dest_path: str) -> tuple[str, str]:
	"""Parse a WebDAV destination path into (frappe_folder, new_name).

	wsgidav may pass the destination either with or without the /dav mount
	prefix; we handle both.
	"""
	dest = dest_path
	if dest.startswith("/dav/"):
		dest = dest[len("/dav"):]
	elif dest in ("/dav", "/dav/"):
		dest = "/"
	dest = dest.rstrip("/")
	parts = [p for p in dest.split("/") if p]
	if not parts:
		raise DAVError(HTTP_FORBIDDEN, "invalid destination")
	new_name = parts[-1]
	parent_parts = parts[:-1]
	new_folder = "Home/" + "/".join(parent_parts) if parent_parts else "Home"
	return new_folder, new_name


class FrappeCollection(DAVCollection):
	"""A Frappe File folder exposed as a WebDAV collection."""

	def __init__(self, path: str, environ: dict, frappe_folder: str) -> None:
		super().__init__(path, environ)
		self.frappe_folder = frappe_folder
		self._meta: Optional[dict] = None

	def _get_meta(self) -> Optional[dict]:
		if self._meta is None:
			parent = frappe_folder_parent(self.frappe_folder)
			if not parent:
				# "Home" is the virtual root — no File doc exists for it
				return None
			name = _folder_display_name(self.frappe_folder)
			self._meta = frappe.db.get_value(
				"File",
				{"file_name": name, "folder": parent, "is_folder": 1},
				["creation", "modified"],
				as_dict=True,
			)
		return self._meta

	def get_creation_date(self) -> Optional[float]:
		meta = self._get_meta()
		return meta.creation.timestamp() if meta else None

	def get_last_modified(self) -> Optional[float]:
		meta = self._get_meta()
		return meta.modified.timestamp() if meta else None

	def get_display_name(self) -> str:
		return _folder_display_name(self.frappe_folder)

	def get_etag(self) -> None:
		return None

	def get_member_names(self) -> list[str]:
		names: list[str] = []

		folders = frappe.get_all(
			"File",
			filters={"folder": self.frappe_folder, "is_folder": 1},
			fields=["file_name"],
		)
		names.extend(f.file_name for f in folders)

		files = frappe.get_all(
			"File",
			filters={"folder": self.frappe_folder, "is_folder": 0},
			fields=["file_name"],
		)
		names.extend(f.file_name for f in files)

		return [n for n in names if not _is_hidden_from_listing(n)]

	def get_member(self, name: str):
		child_path = self.path.rstrip("/") + "/" + name

		if _is_os_metadata(name):
			if child_path in _OS_FILE_STORE:
				return _MemoryFile(child_path, self.environ, name)
			return None

		child_frappe_folder = f"{self.frappe_folder}/{name}"

		if frappe.db.exists("File", {"folder": self.frappe_folder, "file_name": name, "is_folder": 1}):
			return FrappeCollection(child_path + "/", self.environ, child_frappe_folder)

		file_doc = frappe.db.get_value(
			"File",
			{"folder": self.frappe_folder, "file_name": name, "is_folder": 0},
			_FILE_FIELDS,
			as_dict=True,
		)
		if file_doc:
			return FrappeFile(child_path, self.environ, file_doc)

		return None

	def create_empty_resource(self, name: str):
		child_path = self.path.rstrip("/") + "/" + name
		# macOS metadata files (._*, .DS_Store, ...) are handled in-memory —
		# Finder uploads them before the real content and aborts the whole
		# transfer if they fail, but they shouldn't pollute Frappe / S3.
		if _is_os_metadata(name):
			return _MemoryFile(child_path, self.environ, name)
		return FrappeNewFile(child_path, self.environ, self.frappe_folder, name)

	def create_collection(self, name: str):
		"""MKCOL — create a Frappe folder."""
		if frappe.db.exists("File", {"folder": self.frappe_folder, "file_name": name}):
			raise DAVError(HTTP_METHOD_NOT_ALLOWED, "destination already exists")
		try:
			folder = frappe.new_doc("File")
			folder.file_name = name
			folder.folder = self.frappe_folder
			folder.is_folder = 1
			folder.flags.cloud_storage = True
			folder.insert()
			frappe.db.commit()
		except frappe.PermissionError:
			raise DAVError(HTTP_FORBIDDEN)
		except frappe.ValidationError as e:
			raise DAVError(HTTP_FORBIDDEN, str(e))

	def delete(self):
		if self.frappe_folder in _SYSTEM_FOLDERS:
			raise DAVError(HTTP_FORBIDDEN)

		parent = frappe_folder_parent(self.frappe_folder)
		display_name = _folder_display_name(self.frappe_folder)
		folder_name = frappe.db.get_value(
			"File",
			{"folder": parent, "file_name": display_name, "is_folder": 1},
			"name",
		)
		if not folder_name:
			raise DAVError(HTTP_NOT_FOUND)

		try:
			frappe.delete_doc("File", folder_name)
			frappe.db.commit()
		except frappe.PermissionError:
			raise DAVError(HTTP_FORBIDDEN)
		except frappe.ValidationError as e:
			raise DAVError(HTTP_FORBIDDEN, str(e))

	def copy_move_single(self, dest_path: str, is_move: bool):
		"""MOVE — rename or move this folder (and all its descendants)."""
		if not is_move:
			raise DAVError(HTTP_FORBIDDEN, "COPY for folders is not supported")
		if self.frappe_folder in _SYSTEM_FOLDERS:
			raise DAVError(HTTP_FORBIDDEN, "cannot move system folders")

		new_parent, new_name = _parse_dest(dest_path)
		new_frappe_folder = f"{new_parent}/{new_name}"

		parent = frappe_folder_parent(self.frappe_folder)
		display = _folder_display_name(self.frappe_folder)
		folder_name = frappe.db.get_value(
			"File",
			{"folder": parent, "file_name": display, "is_folder": 1},
			"name",
		)
		if not folder_name:
			raise DAVError(HTTP_NOT_FOUND)
		if frappe.db.exists(
			"File",
			{"folder": new_parent, "file_name": new_name, "is_folder": 1, "name": ["!=", folder_name]},
		):
			raise DAVError(HTTP_METHOD_NOT_ALLOWED, "destination already exists")

		try:
			frappe.db.set_value("File", folder_name, "file_name", new_name)
			frappe.db.set_value("File", folder_name, "folder", new_parent)
			# Reparent every descendant: any File whose `folder` starts with the
			# old path (including itself) gets that prefix swapped for the new.
			old = self.frappe_folder
			affected = frappe.get_all(
				"File",
				or_filters=[
					["folder", "=", old],
					["folder", "like", f"{old}/%"],
				],
				fields=["name", "folder"],
			)
			for f in affected:
				new_folder = new_frappe_folder + f.folder[len(old):]
				frappe.db.set_value("File", f.name, "folder", new_folder)
			frappe.db.commit()
		except frappe.PermissionError:
			raise DAVError(HTTP_FORBIDDEN)
		except frappe.ValidationError as e:
			raise DAVError(HTTP_FORBIDDEN, str(e))

	def support_recursive_delete(self) -> bool:
		return False

	def support_recursive_move(self, dest_path: str) -> bool:
		# We rename/move the folder doc + reparent descendants in one transaction
		# inside copy_move_single — much faster than wsgidav's per-child loop.
		return True


_FILE_FIELDS = [
	"name",
	"file_name",
	"file_size",
	"content_hash",
	"modified",
	"creation",
	"s3_key",
	"is_private",
	"file_url",
]


class _WriteBuffer(io.RawIOBase):
	"""Accumulates a WebDAV PUT body; on close creates a Frappe File doc and uploads to S3.

	macOS Finder uses a two-step upload: PUT Content-Length: 0 to claim the path,
	then LOCK, then a second PUT with the real content. When content is empty we
	create a placeholder doc so the LOCK handler can find the resource via
	get_resource_inst() — without it, wsgidav's lock-discovery crashes on NoneType.
	"""

	def __init__(self, file_name: str, frappe_folder: str, content_type: Optional[str]) -> None:
		self._buf = io.BytesIO()
		self._file_name = file_name
		self._frappe_folder = frappe_folder
		self._content_type = content_type

	def write(self, data: bytes) -> int:
		return self._buf.write(data)

	def close(self) -> None:
		if not self.closed:
			super().close()  # mark closed first so double-close is a no-op
			try:
				self._commit()
			except Exception:
				frappe.log_error("WebDAV upload error", frappe.get_traceback())
				raise
		else:
			super().close()

	def _commit(self) -> None:
		from frappe.core.doctype.file.utils import get_content_hash
		from cloud_storage.cloud_storage.overrides.file import upload_file

		content = self._buf.getvalue()

		file_doc = frappe.new_doc("File")
		file_doc.file_name = self._file_name
		file_doc.folder = self._frappe_folder
		file_doc.is_private = 0
		# flags.cloud_storage skips validate_file_url (file_url is set later by upload_file)
		file_doc.flags.cloud_storage = True

		if not content:
			# Empty body: create a placeholder so LOCK can find the resource.
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
			file_doc.save_file_on_filesystem()
		else:
			upload_file(file_doc)
		frappe.db.commit()


class _OverwriteBuffer(io.RawIOBase):
	"""Accumulates a WebDAV PUT body for an existing resource; on close uploads to S3
	and updates the Frappe File doc in place.

	Used by FrappeFile.begin_write() for Finder's two-step upload (the second PUT
	that follows the empty-placeholder PUT and the LOCK).
	"""

	def __init__(self, doc_name: str) -> None:
		self._buf = io.BytesIO()
		self._doc_name = doc_name

	def write(self, data: bytes) -> int:
		return self._buf.write(data)

	def close(self) -> None:
		if not self.closed:
			super().close()
			try:
				self._commit()
			except Exception:
				frappe.log_error("WebDAV overwrite error", frappe.get_traceback())
				raise
		else:
			super().close()

	def _commit(self) -> None:
		from frappe.core.doctype.file.utils import get_content_hash
		from cloud_storage.cloud_storage.overrides.file import upload_file

		content = self._buf.getvalue()
		if not content:
			return

		content_hash = get_content_hash(content)
		file_doc = frappe.get_doc("File", self._doc_name)
		file_doc.content = content
		file_doc.content_hash = content_hash
		file_doc.file_size = len(content)
		# content_type is not a stored field; set it so upload_file can read it.
		mime, _ = mimetypes.guess_type(file_doc.file_name or "")
		file_doc.content_type = mime or "application/octet-stream"
		file_doc.flags.cloud_storage = True

		config = frappe.conf.get("cloud_storage_settings", {})
		if not config or config.get("use_local"):
			file_doc.save_file_on_filesystem()
		else:
			upload_file(file_doc)
			file_doc.db_set("file_size", len(content))
			file_doc.db_set("content_hash", content_hash)
		frappe.db.commit()


class _MemoryBuffer(io.RawIOBase):
	"""Write buffer that stashes content in _OS_FILE_STORE on close."""

	def __init__(self, path: str) -> None:
		self._buf = io.BytesIO()
		self._path = path

	def write(self, data: bytes) -> int:
		return self._buf.write(data)

	def close(self) -> None:
		if not self.closed:
			super().close()
			_OS_FILE_STORE[self._path] = self._buf.getvalue()
		else:
			super().close()


class _MemoryFile(DAVNonCollection):
	"""In-memory resource for OS metadata files (._*, .DS_Store, ...)."""

	def __init__(self, path: str, environ: dict, file_name: str) -> None:
		super().__init__(path, environ)
		self.file_name = file_name

	def _content(self) -> bytes:
		return _OS_FILE_STORE.get(self.path, b"")

	def get_content_length(self) -> int:
		return len(self._content())

	def get_content_type(self) -> str:
		return "application/octet-stream"

	def get_last_modified(self) -> None:
		return None

	def get_creation_date(self) -> None:
		return None

	def get_etag(self) -> None:
		return None

	def get_display_name(self) -> str:
		return self.file_name

	def support_etag(self) -> bool:
		return False

	def support_content_length(self) -> bool:
		return True

	def support_modified(self) -> bool:
		return False

	def get_content(self) -> io.RawIOBase:
		return io.BytesIO(self._content())

	def begin_write(self, content_type: Optional[str] = None):
		return _MemoryBuffer(self.path)

	def delete(self):
		_OS_FILE_STORE.pop(self.path, None)

	def copy_move_single(self, dest_path: str, is_move: bool):
		raise DAVError(HTTP_FORBIDDEN)


class FrappeNewFile(DAVNonCollection):
	"""Transient resource representing a file being uploaded via WebDAV PUT."""

	def __init__(self, path: str, environ: dict, frappe_folder: str, file_name: str) -> None:
		super().__init__(path, environ)
		self.frappe_folder = frappe_folder
		self.file_name = file_name

	def get_content_length(self) -> None:
		return None

	def get_content_type(self) -> str:
		mime, _ = mimetypes.guess_type(self.file_name or "")
		return mime or "application/octet-stream"

	def get_last_modified(self) -> None:
		return None

	def get_creation_date(self) -> None:
		return None

	def get_etag(self) -> None:
		return None

	def get_display_name(self) -> str:
		return self.file_name

	def support_etag(self) -> bool:
		return False

	def support_content_length(self) -> bool:
		return False

	def support_modified(self) -> bool:
		return False

	def get_content(self):
		raise DAVError(HTTP_NOT_FOUND)

	def begin_write(self, content_type: Optional[str] = None):
		return _WriteBuffer(self.file_name, self.frappe_folder, content_type)

	def delete(self):
		raise DAVError(HTTP_FORBIDDEN)

	def copy_move_single(self, dest_path: str, is_move: bool):
		raise DAVError(HTTP_FORBIDDEN)


class FrappeFile(DAVNonCollection):
	"""A Frappe File record exposed as a WebDAV resource."""

	def __init__(self, path: str, environ: dict, file_doc: dict) -> None:
		super().__init__(path, environ)
		self.file_doc = file_doc

	def get_content_length(self) -> int:
		return self.file_doc.file_size or 0

	def get_content_type(self) -> str:
		mime, _ = mimetypes.guess_type(self.file_doc.file_name or "")
		return mime or "application/octet-stream"

	def get_last_modified(self) -> Optional[float]:
		if self.file_doc.modified:
			return self.file_doc.modified.timestamp()
		return None

	def get_creation_date(self) -> Optional[float]:
		if self.file_doc.creation:
			return self.file_doc.creation.timestamp()
		return None

	def get_etag(self) -> Optional[str]:
		return self.file_doc.content_hash or None

	def get_display_name(self) -> str:
		return self.file_doc.file_name

	def support_etag(self) -> bool:
		return bool(self.file_doc.content_hash)

	def support_content_length(self) -> bool:
		return True

	def support_modified(self) -> bool:
		return True

	def support_ranges(self) -> bool:
		return False

	def get_content(self) -> io.RawIOBase:
		from cloud_storage.cloud_storage.overrides.file import get_cloud_storage_client

		if self.file_doc.s3_key:
			client = get_cloud_storage_client()
			response = client.get_object(Bucket=client.bucket, Key=self.file_doc.s3_key)
			return response["Body"]

		if not self.file_doc.file_size:
			# Empty placeholder created by Finder's two-step PUT (CL=0 then content).
			# No s3_key and no on-disk file yet — return an empty stream.
			return io.BytesIO(b"")

		if self.file_doc.is_private:
			file_path = frappe.get_site_path("private", "files", self.file_doc.file_name)
		else:
			file_path = frappe.get_site_path("public", "files", self.file_doc.file_name)
		return open(file_path, "rb")

	def begin_write(self, content_type: Optional[str] = None):
		return _OverwriteBuffer(self.file_doc.name)

	def delete(self):
		try:
			# Clear associations first so the hook proceeds to _delete_file_on_disk.
			frappe.db.delete("File Association", {"parent": self.file_doc.name})
			frappe.delete_doc("File", self.file_doc.name)
			frappe.db.commit()
		except frappe.PermissionError:
			raise DAVError(HTTP_FORBIDDEN)
		except frappe.ValidationError as e:
			raise DAVError(HTTP_FORBIDDEN, str(e))

	def copy_move_single(self, dest_path: str, is_move: bool):
		"""MOVE — rename and/or move this file (S3 object included)."""
		if not is_move:
			raise DAVError(HTTP_FORBIDDEN, "COPY is not supported")

		new_folder, new_name = _parse_dest(dest_path)

		# WebDAV semantics: MOVE overwrites the destination if it exists.
		existing = frappe.db.get_value(
			"File",
			{"folder": new_folder, "file_name": new_name, "is_folder": 0, "name": ["!=", self.file_doc.name]},
			"name",
		)
		if existing:
			try:
				frappe.db.delete("File Association", {"parent": existing})
				frappe.delete_doc("File", existing, ignore_permissions=False)
			except frappe.PermissionError:
				raise DAVError(HTTP_FORBIDDEN)

		old_name = self.file_doc.file_name
		old_s3_key = self.file_doc.s3_key
		doc_name = self.file_doc.name

		try:
			frappe.db.set_value("File", doc_name, "file_name", new_name)
			frappe.db.set_value("File", doc_name, "folder", new_folder)

			# Legacy S3 paths include the file_name → renaming requires S3 rename
			# (copy to new key + delete old). A folder-only move keeps the key.
			if old_s3_key and old_name != new_name:
				from cloud_storage.cloud_storage.overrides.file import (
					FILE_URL,
					get_cloud_storage_client,
					get_file_path,
				)

				reloaded = frappe.get_doc("File", doc_name)
				client = get_cloud_storage_client()
				new_key = get_file_path(reloaded, client.folder)
				client.copy_object(
					Bucket=client.bucket,
					CopySource={"Bucket": client.bucket, "Key": old_s3_key},
					Key=new_key,
				)
				client.delete_object(Bucket=client.bucket, Key=old_s3_key)
				frappe.db.set_value("File", doc_name, "s3_key", new_key)
				frappe.db.set_value("File", doc_name, "file_url", FILE_URL.format(path=new_key))

			frappe.db.commit()
		except frappe.PermissionError:
			raise DAVError(HTTP_FORBIDDEN)
		except frappe.ValidationError as e:
			raise DAVError(HTTP_FORBIDDEN, str(e))

	def support_recursive_delete(self) -> bool:
		return False

	def support_recursive_move(self, dest_path: str) -> bool:
		return False


class FrappeDAVProvider(DAVProvider):
	"""Root WebDAV provider: maps URL paths to Frappe File doctypes."""

	def get_resource_inst(self, path: str, environ: dict):
		"""Return the DAVCollection or DAVNonCollection for the given path, or None."""
		# wsgidav passes PATH_INFO (already /dav-stripped by our middleware) for
		# the request resource, but the Destination header for MOVE/COPY arrives
		# with the full URL path including /dav. Strip it here so both flows
		# resolve to the same Frappe folder tree.
		if path.startswith("/dav/"):
			path = path[len("/dav"):]
		elif path in ("/dav", "/dav/"):
			path = "/"
		norm = path.rstrip("/")

		if not norm:
			return FrappeCollection("/", environ, "Home")

		parts = [p for p in norm.split("/") if p]

		# Walk intermediate folder segments to build the parent folder path
		frappe_folder = "Home"
		for part in parts[:-1]:
			parent = frappe_folder
			if not frappe.db.exists("File", {"folder": parent, "file_name": part, "is_folder": 1}):
				return None
			frappe_folder = f"{parent}/{part}"

		last = parts[-1]

		# OS metadata files live only in _OS_FILE_STORE, not in Frappe.
		if _is_os_metadata(last):
			if path in _OS_FILE_STORE:
				return _MemoryFile(path, environ, last)
			return None

		# Last segment: folder?
		if frappe.db.exists("File", {"folder": frappe_folder, "file_name": last, "is_folder": 1}):
			child_folder = f"{frappe_folder}/{last}"
			canonical = path if path.endswith("/") else path + "/"
			return FrappeCollection(canonical, environ, child_folder)

		# Last segment: file?
		file_doc = frappe.db.get_value(
			"File",
			{"folder": frappe_folder, "file_name": last, "is_folder": 0},
			_FILE_FIELDS,
			as_dict=True,
		)
		if file_doc:
			return FrappeFile(path, environ, file_doc)

		return None

	def is_readonly(self) -> bool:
		return False


def frappe_folder_parent(frappe_folder: str) -> str:
	"""Return the parent folder of a Frappe folder path.

	Home/Attachments → Home
	Home             → (empty string, root)
	"""
	if "/" not in frappe_folder:
		return ""
	return frappe_folder.rsplit("/", 1)[0]
