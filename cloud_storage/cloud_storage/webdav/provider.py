# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt
# Maps Frappe File/folder doctypes to wsgidav DAV resources.

import io
import mimetypes
import uuid
from typing import Any
from urllib.parse import unquote

import frappe
from frappe.model.rename_doc import rename_doc
from wsgidav.dav_error import (
	DAVError,
	HTTP_FORBIDDEN,
	HTTP_METHOD_NOT_ALLOWED,
	HTTP_NOT_FOUND,
)
from wsgidav.dav_provider import DAVCollection, DAVNonCollection, DAVProvider

from cloud_storage.cloud_storage.overrides.file import FILE_URL, get_cloud_storage_client
from cloud_storage.cloud_storage.webdav import memory as os_file_store
from cloud_storage.cloud_storage.webdav import paths
from cloud_storage.cloud_storage.webdav.buffers import (
	MemoryBuffer as _MemoryBuffer,
	OverwriteBuffer as _OverwriteBuffer,
	WriteBuffer as _WriteBuffer,
)
from cloud_storage.cloud_storage.webdav.permissions import (
	can as _can,
	can_create as _can_create,
	can_read_folder as _can_read_folder,
	can_write_folder as _can_write_folder,
	folder_display_name as _folder_display_name,
	folder_parent as frappe_folder_parent,
)


SYSTEM_FOLDERS = {"Home", "Home/Attachments"}
OS_FILES = frozenset({".DS_Store", "desktop.ini", "Thumbs.db", ".Trashes", ".Spotlight-V100"})
OS_PREFIXES = ("._",)
TEMP_PREFIXES = (".sb-",)


def is_os_metadata(name: str) -> bool:
	return name in OS_FILES or name.startswith(OS_PREFIXES)


def is_hidden_from_listing(name: str) -> bool:
	return is_os_metadata(name) or name.startswith(TEMP_PREFIXES)


MOUNT_PREFIX = "/dav"
logger = frappe.logger("webdav", allow_site=False)


def is_cloud_storage_enabled() -> bool:
	config = frappe.conf.get("cloud_storage_settings", {})
	return bool(config and not config.get("use_local"))


def detach_local_file_before_delete(file_doc) -> None:
	"""Delete a transient File doc without deleting the shared local payload."""
	if is_cloud_storage_enabled() or file_doc.s3_key:
		return
	file_doc.file_url = None
	file_doc.db_set("file_url", None)


def backup_s3_object(key: str | None) -> tuple[Any, str, str] | None:
	if not key or not is_cloud_storage_enabled():
		return None
	client = get_cloud_storage_client()
	backup_key = f"{key}.webdav-overwrite-backup-{uuid.uuid4().hex}"
	client.copy_object(
		Bucket=client.bucket,
		CopySource={"Bucket": client.bucket, "Key": key},
		Key=backup_key,
	)
	return client, key, backup_key


def delete_s3_backup(backup: tuple[Any, str, str] | None) -> None:
	if not backup:
		return
	client, _, backup_key = backup
	try:
		client.delete_object(Bucket=client.bucket, Key=backup_key)
	except Exception:
		logger.warning(f"failed to remove WebDAV overwrite backup {backup_key!r}")


def restore_s3_backup(backup: tuple[Any, str, str] | None) -> None:
	if not backup:
		return
	client, original_key, backup_key = backup
	try:
		client.copy_object(
			Bucket=client.bucket,
			CopySource={"Bucket": client.bucket, "Key": backup_key},
			Key=original_key,
		)
	except Exception:
		logger.warning(f"failed to restore WebDAV overwrite backup {backup_key!r}")
		raise
	finally:
		delete_s3_backup(backup)


def strip_dav_prefix(path: str) -> str:
	"""Strip /dav prefix so both PATH_INFO and Destination headers resolve uniformly."""
	if path.startswith(MOUNT_PREFIX + "/"):
		return path[len(MOUNT_PREFIX) :]
	if path in (MOUNT_PREFIX, MOUNT_PREFIX + "/"):
		return "/"
	return path


def split_dav_path(path: str) -> list[str]:
	path = strip_dav_prefix(path).strip("/")
	parts = [unquote(part) for part in path.split("/") if part]
	if any(part in (".", "..") or "/" in part or "\\" in part for part in parts):
		raise DAVError(HTTP_FORBIDDEN, "invalid path segment")
	return parts


def parse_dest(dest_path: str) -> tuple[str, str]:
	parts = split_dav_path(dest_path)
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
		self._meta: Any | None = None

	def get_meta(self) -> Any | None:
		if self._meta is None:
			parent = frappe_folder_parent(self.frappe_folder)
			if not parent:
				return None
			name = _folder_display_name(self.frappe_folder)
			self._meta = frappe.db.get_value(
				"File",
				{"file_name": name, "folder": parent, "is_folder": 1},
				["creation", "modified"],
				as_dict=True,
			)
		return self._meta

	def get_creation_date(self) -> float | None:
		meta = self.get_meta()
		return meta.creation.timestamp() if meta else None

	def get_last_modified(self) -> float | None:
		meta = self.get_meta()
		return meta.modified.timestamp() if meta else None

	def get_display_name(self) -> str:
		return _folder_display_name(self.frappe_folder)

	def get_etag(self) -> None:
		return None

	def get_member_names(self) -> list[str]:
		# get_list (unlike get_all) applies file_permission_query_conditions,
		# including folder-inherited shares — the sole read-visibility gate.
		rows = frappe.get_list(
			"File",
			filters={"folder": self.frappe_folder},
			fields=["name", "file_name"],
		)
		return [r.file_name for r in rows if not is_hidden_from_listing(r.file_name)]

	def get_member(self, name: str):
		child_path = self.path.rstrip("/") + "/" + name

		if is_os_metadata(name):
			if not _can_read_folder(self.frappe_folder):
				return None
			if os_file_store.contains(child_path):
				return _MemoryFile(child_path, self.environ, name, self.frappe_folder)
			return None

		folder_match = frappe.get_list(
			"File",
			filters={"folder": self.frappe_folder, "file_name": name, "is_folder": 1},
			pluck="name",
			limit_page_length=1,
		)
		if folder_match:
			child_frappe_folder = f"{self.frappe_folder}/{name}"
			return FrappeCollection(child_path + "/", self.environ, child_frappe_folder)

		file_rows = frappe.get_list(
			"File",
			filters={"folder": self.frappe_folder, "file_name": name, "is_folder": 0},
			fields=FILE_FIELDS,
			limit_page_length=1,
		)
		if file_rows:
			return FrappeFile(child_path, self.environ, file_rows[0])

		return None

	def create_empty_resource(self, name: str):
		child_path = self.path.rstrip("/") + "/" + name
		if not _can_create():
			raise DAVError(HTTP_FORBIDDEN)
		if not _can_write_folder(self.frappe_folder):
			raise DAVError(HTTP_FORBIDDEN)
		if is_os_metadata(name):
			return _MemoryFile(child_path, self.environ, name, self.frappe_folder)
		return FrappeNewFile(child_path, self.environ, self.frappe_folder, name)

	def create_collection(self, name: str):
		if not _can_create():
			raise DAVError(HTTP_FORBIDDEN)
		if not _can_write_folder(self.frappe_folder):
			raise DAVError(HTTP_FORBIDDEN)
		if frappe.db.exists("File", {"folder": self.frappe_folder, "file_name": name}):
			raise DAVError(HTTP_METHOD_NOT_ALLOWED, "destination already exists")
		try:
			folder = frappe.new_doc("File")
			folder.file_name = name
			folder.folder = self.frappe_folder
			folder.is_folder = 1
			folder.is_private = 1
			folder.flags.cloud_storage = True
			folder.insert()
			frappe.db.commit()
		except frappe.PermissionError:
			raise DAVError(HTTP_FORBIDDEN)
		except frappe.ValidationError as e:
			raise DAVError(HTTP_FORBIDDEN, str(e))

	def delete(self):
		parent = frappe_folder_parent(self.frappe_folder)
		display_name = _folder_display_name(self.frappe_folder)
		folder_name = frappe.db.get_value(
			"File",
			{"folder": parent, "file_name": display_name, "is_folder": 1},
			"name",
		)
		if not folder_name:
			raise DAVError(HTTP_NOT_FOUND)
		if not _can(folder_name, "delete"):
			raise DAVError(HTTP_FORBIDDEN)
		try:
			frappe.delete_doc("File", folder_name)
			frappe.db.commit()
		except frappe.PermissionError:
			raise DAVError(HTTP_FORBIDDEN)
		except frappe.ValidationError as e:
			raise DAVError(HTTP_FORBIDDEN, str(e))

	def handle_copy(self, dest_path: str, *, depth_infinity: bool) -> bool:
		# COPY is not supported. Raising here prevents wsgidav from deleting
		# the destination before discovering copy_move_single raises too.
		raise DAVError(HTTP_FORBIDDEN)

	def handle_move(self, dest_path: str) -> bool | list:
		# Handle MOVE natively so wsgidav never reaches its fallback path that
		# deletes an existing destination before calling move_recursive().
		if self.frappe_folder in SYSTEM_FOLDERS:
			raise DAVError(HTTP_FORBIDDEN, "cannot move system folders")
		parent = frappe_folder_parent(self.frappe_folder)
		display = _folder_display_name(self.frappe_folder)
		folder_name = frappe.db.get_value(
			"File",
			{"folder": parent, "file_name": display, "is_folder": 1},
			"name",
		)
		if not folder_name or not _can(folder_name, "write"):
			raise DAVError(HTTP_FORBIDDEN)
		new_parent, _ = parse_dest(dest_path)
		if not _can_write_folder(new_parent):
			raise DAVError(HTTP_FORBIDDEN)
		return self.move_recursive(dest_path)

	def copy_move_single(self, dest_path: str, *, is_move: bool):
		raise DAVError(HTTP_FORBIDDEN)

	def move_recursive(self, dest_path: str):
		if self.frappe_folder in SYSTEM_FOLDERS:
			raise DAVError(HTTP_FORBIDDEN, "cannot move system folders")

		new_parent, new_name = parse_dest(dest_path)
		new_frappe_folder = f"{new_parent}/{new_name}"
		logger.debug(f"folder move src={self.frappe_folder!r} → {new_frappe_folder!r}")

		parent = frappe_folder_parent(self.frappe_folder)
		display = _folder_display_name(self.frappe_folder)
		folder_name = frappe.db.get_value(
			"File",
			{"folder": parent, "file_name": display, "is_folder": 1},
			"name",
		)
		if not folder_name:
			raise DAVError(HTTP_NOT_FOUND)
		if not _can(folder_name, "write"):
			raise DAVError(HTTP_FORBIDDEN)
		if not _can_write_folder(new_parent):
			raise DAVError(HTTP_FORBIDDEN)
		if frappe.db.exists(
			"File",
			{"folder": new_parent, "file_name": new_name, "is_folder": 1, "name": ["!=", folder_name]},
		):
			raise DAVError(HTTP_METHOD_NOT_ALLOWED, "destination already exists")

		savepoint = "webdav_folder_move"
		frappe.db.savepoint(savepoint)
		try:
			# Frappe stores folder path in both `name` (PK) and display fields
			# (`file_name`, `folder`). The WebDAV path may differ from the
			# current PK after an ancestor move, so update/rename by folder_name.
			if new_name != display:
				frappe.db.set_value("File", folder_name, "file_name", new_name)
			if new_parent != parent:
				frappe.db.set_value("File", folder_name, "folder", new_parent)

			if new_frappe_folder != folder_name:
				rename_doc(
					"File",
					folder_name,
					new_frappe_folder,
					merge=False,
					force=True,
					show_alert=False,
					validate=False,
				)

			# Reparent descendants by current folder path. Their File.name may
			# still be an old PK, but their `folder` value follows the visible path.
			old = self.frappe_folder
			affected = frappe.get_all(
				"File",
				or_filters=[
					["folder", "=", old],
					["folder", "like", f"{old}/%"],
				],
				fields=["name", "folder"],
			)
			logger.debug(f"folder move reparenting {len(affected)} descendant(s)")
			for f in affected:
				new_folder_path = new_frappe_folder + f.folder[len(old) :]
				frappe.db.set_value("File", f.name, "folder", new_folder_path)
			frappe.db.commit()
			return []
		except Exception as exc:
			frappe.db.rollback(save_point=savepoint)
			if isinstance(exc, frappe.PermissionError):
				raise DAVError(HTTP_FORBIDDEN)
			if isinstance(exc, frappe.ValidationError):
				raise DAVError(HTTP_FORBIDDEN, str(exc))
			raise

	def support_recursive_delete(self) -> bool:
		return False

	def support_recursive_move(self, dest_path: str) -> bool:
		return True


FILE_FIELDS = [
	"name",
	"file_name",
	"folder",
	"file_size",
	"content_hash",
	"modified",
	"creation",
	"s3_key",
	"is_private",
	"file_url",
]


class _MemoryFile(DAVNonCollection):
	"""In-memory resource for OS metadata files (._*, .DS_Store, ...)."""

	def __init__(self, path: str, environ: dict, file_name: str, frappe_folder: str) -> None:
		super().__init__(path, environ)
		self.file_name = file_name
		self.frappe_folder = frappe_folder

	def raw_content(self) -> bytes:
		return os_file_store.get(self.path) or b""

	def get_content_length(self) -> int:
		return len(self.raw_content())

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

	def get_content(self) -> Any:
		return io.BytesIO(self.raw_content())

	def begin_write(self, content_type: str | None = None):
		if not _can_write_folder(self.frappe_folder):
			raise DAVError(HTTP_FORBIDDEN)
		return _MemoryBuffer(self.path)

	def delete(self):
		if not _can_write_folder(self.frappe_folder):
			raise DAVError(HTTP_FORBIDDEN)
		os_file_store.delete(self.path)

	def handle_move(self, dest_path: str) -> bool:
		if not _can_write_folder(self.frappe_folder):
			raise DAVError(HTTP_FORBIDDEN)
		new_folder, new_name = parse_dest(dest_path)
		if not _can_write_folder(new_folder):
			raise DAVError(HTTP_FORBIDDEN)
		dav_folder = new_folder[len("Home") :]
		new_key = f"{dav_folder}/{new_name}" if dav_folder else f"/{new_name}"
		os_file_store.set(new_key, self.raw_content())
		os_file_store.delete(self.path)
		return True

	def copy_move_single(self, dest_path: str, *, is_move: bool):
		raise DAVError(HTTP_FORBIDDEN)

	def support_recursive_move(self, dest_path: str) -> bool:
		return False


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

	def begin_write(self, content_type: str | None = None):
		return _WriteBuffer(self.file_name, self.frappe_folder, content_type)

	def delete(self):
		raise DAVError(HTTP_FORBIDDEN)

	def copy_move_single(self, dest_path: str, *, is_move: bool):
		raise DAVError(HTTP_FORBIDDEN)


class FrappeFile(DAVNonCollection):
	"""A Frappe File record exposed as a WebDAV resource."""

	def __init__(self, path: str, environ: dict, file_doc: Any) -> None:
		super().__init__(path, environ)
		self.file_doc = file_doc

	def get_content_length(self) -> int:
		return self.file_doc.file_size or 0

	def get_content_type(self) -> str:
		mime, _ = mimetypes.guess_type(self.file_doc.file_name or "")
		return mime or "application/octet-stream"

	def get_last_modified(self) -> float | None:
		if self.file_doc.modified:
			return self.file_doc.modified.timestamp()
		return None

	def get_creation_date(self) -> float | None:
		if self.file_doc.creation:
			return self.file_doc.creation.timestamp()
		return None

	def get_etag(self) -> str | None:
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

	def get_content(self) -> Any:
		if self.file_doc.s3_key:
			client = get_cloud_storage_client()
			response = client.get_object(Bucket=client.bucket, Key=self.file_doc.s3_key)
			return response["Body"]

		if not self.file_doc.file_size:
			# Empty placeholder from Finder's CL=0 PUT — no content yet.
			return io.BytesIO(b"")

		# Local mode: resolve path from file_url to handle any name sanitization.
		file_doc = frappe.get_doc("File", self.file_doc.name)
		file_path = file_doc.get_full_path()
		return open(file_path, "rb")

	def begin_write(self, content_type: str | None = None):
		if not _can(self.file_doc.name, "write"):
			raise DAVError(HTTP_FORBIDDEN)
		return _OverwriteBuffer(self.file_doc.name)

	def delete(self):
		if not _can(self.file_doc.name, "delete"):
			raise DAVError(HTTP_FORBIDDEN)
		try:
			frappe.delete_doc("File", self.file_doc.name)
			frappe.db.commit()
		except frappe.PermissionError:
			raise DAVError(HTTP_FORBIDDEN)
		except frappe.ValidationError as e:
			raise DAVError(HTTP_FORBIDDEN, str(e))

	def handle_copy(self, dest_path: str, *, depth_infinity: bool) -> bool:
		raise DAVError(HTTP_FORBIDDEN)

	def handle_move(self, dest_path: str) -> bool | list:
		# Handle MOVE natively so wsgidav never reaches its fallback path that
		# deletes an existing destination before calling move_recursive().
		if not _can(self.file_doc.name, "write"):
			raise DAVError(HTTP_FORBIDDEN)
		new_folder, _ = parse_dest(dest_path)
		if not _can_write_folder(new_folder):
			raise DAVError(HTTP_FORBIDDEN)
		return self.move_recursive(dest_path)

	def copy_move_single(self, dest_path: str, *, is_move: bool):
		raise DAVError(HTTP_FORBIDDEN)

	def move_recursive(self, dest_path: str):
		if not _can(self.file_doc.name, "write"):
			raise DAVError(HTTP_FORBIDDEN)
		new_folder, new_name = parse_dest(dest_path)
		if not _can_write_folder(new_folder):
			raise DAVError(HTTP_FORBIDDEN)
		logger.debug(
			f"file move doc={self.file_doc.name} {self.file_doc.file_name!r} → " f"{new_folder}/{new_name}"
		)

		# MOVE overwrites the destination if it exists. Preserve the destination
		# File doc so Cloud Storage's File Version table keeps its history.
		existing = frappe.db.get_value(
			"File",
			{
				"folder": new_folder,
				"file_name": new_name,
				"is_folder": 0,
				"name": ["!=", self.file_doc.name],
			},
			["name", "s3_key"],
			as_dict=True,
		)

		if existing:
			if not _can(existing.name, "write"):
				raise DAVError(HTTP_FORBIDDEN)
			return self.replace_existing_destination(existing.name, existing.s3_key)

		displaced = self.find_displaced_atomic_save_destination(new_folder, new_name)
		if displaced:
			if not _can(displaced.name, "write"):
				raise DAVError(HTTP_FORBIDDEN)
			return self.replace_existing_destination(
				displaced.name,
				displaced.s3_key,
				final_folder=new_folder,
				final_name=new_name,
			)

		old_name = self.file_doc.file_name
		old_s3_key = self.file_doc.s3_key
		is_rename = bool(old_s3_key and old_name != new_name)

		# S3 rename order: copy → commit DB → delete old key.
		# If commit fails, clean up the new source copy and restore overwritten S3.
		client = None
		new_key = None
		savepoint = "webdav_file_move"
		frappe.db.savepoint(savepoint)
		try:
			file_doc = frappe.get_doc("File", self.file_doc.name)
			file_doc.file_name = new_name
			file_doc.folder = new_folder
			file_doc.flags.cloud_storage = True

			if is_rename:
				client = get_cloud_storage_client()
				new_key = paths.get_webdav_path(file_doc, client.folder)
				logger.debug(f"file move S3 rename {old_s3_key!r} → {new_key!r}")
				client.copy_object(
					Bucket=client.bucket,
					CopySource={"Bucket": client.bucket, "Key": old_s3_key},
					Key=new_key,
				)
				file_doc.s3_key = new_key
				file_doc.file_url = FILE_URL.format(path=new_key)

			file_doc.save()
			frappe.db.commit()
		except Exception as exc:
			frappe.db.rollback(save_point=savepoint)
			if is_rename and client is not None and new_key:
				try:
					client.delete_object(Bucket=client.bucket, Key=new_key)
				except Exception:
					logger.warning(f"failed to clean up orphan S3 key {new_key!r}")
			if isinstance(exc, frappe.PermissionError):
				raise DAVError(HTTP_FORBIDDEN)
			if isinstance(exc, frappe.ValidationError):
				raise DAVError(HTTP_FORBIDDEN, str(exc))
			raise
		if is_rename and client is not None:
			try:
				client.delete_object(Bucket=client.bucket, Key=old_s3_key)
			except Exception:
				logger.warning(f"failed to remove old S3 key {old_s3_key!r} after rename")
		return []

	def find_displaced_atomic_save_destination(self, new_folder: str, new_name: str):
		source_folder = self.file_doc.get("folder")
		if self.file_doc.file_name != new_name or not source_folder:
			return None
		if frappe_folder_parent(source_folder) != new_folder:
			return None

		source_folder_name = _folder_display_name(source_folder)
		if not source_folder_name.startswith(f"{new_name}.sb-"):
			return None

		prefix = source_folder_name.rsplit("-", 1)[0] + "-"
		rows = frappe.get_all(
			"File",
			filters={"folder": new_folder, "is_folder": 0, "name": ["!=", self.file_doc.name]},
			fields=["name", "file_name", "s3_key", "modified"],
			order_by="modified desc",
		)
		for row in rows:
			if row.file_name.startswith(prefix):
				logger.debug(f"atomic save replace final={new_folder}/{new_name!r} displaced={row.name!r}")
				return row
		return None

	def replace_existing_destination(
		self,
		existing_name: str,
		existing_s3_key: str | None,
		*,
		final_folder: str | None = None,
		final_name: str | None = None,
	):
		savepoint = "webdav_file_replace"
		overwrite_backup = None
		source_backup = None
		frappe.db.savepoint(savepoint)
		try:
			source_doc = frappe.get_doc("File", self.file_doc.name)
			existing_doc = frappe.get_doc("File", existing_name)
			overwrite_backup = backup_s3_object(existing_s3_key)
			source_backup = backup_s3_object(source_doc.s3_key)

			if final_folder is not None:
				existing_doc.folder = final_folder
			if final_name is not None:
				existing_doc.file_name = final_name
			if final_folder is not None or final_name is not None:
				existing_doc.flags.cloud_storage = True
				existing_doc.save()

			paths.replace_existing_via_webdav(existing_doc, source_doc)
			detach_local_file_before_delete(source_doc)
			frappe.delete_doc("File", source_doc.name)
			frappe.db.commit()
			current_s3_key = frappe.db.get_value("File", existing_doc.name, "s3_key")
			if existing_s3_key and existing_s3_key != current_s3_key:
				client = get_cloud_storage_client()
				try:
					client.delete_object(Bucket=client.bucket, Key=existing_s3_key)
				except Exception:
					logger.warning(f"failed to remove old S3 key {existing_s3_key!r} after replace")
			delete_s3_backup(overwrite_backup)
			delete_s3_backup(source_backup)
			return []
		except Exception as exc:
			frappe.db.rollback(save_point=savepoint)
			restore_s3_backup(overwrite_backup)
			restore_s3_backup(source_backup)
			if isinstance(exc, frappe.PermissionError):
				raise DAVError(HTTP_FORBIDDEN)
			if isinstance(exc, frappe.ValidationError):
				raise DAVError(HTTP_FORBIDDEN, str(exc))
			raise

	def support_recursive_delete(self) -> bool:
		return False

	def support_recursive_move(self, dest_path: str) -> bool:
		return True


class FrappeDAVProvider(DAVProvider):
	"""Root WebDAV provider: maps URL paths to Frappe File doctypes."""

	def get_resource_inst(self, path: str, environ: dict):
		path = strip_dav_prefix(path)
		parts = split_dav_path(path)

		if not parts:
			return FrappeCollection("/", environ, "Home")

		frappe_folder = "Home"
		for part in parts[:-1]:
			parent = frappe_folder
			match = frappe.get_list(
				"File",
				filters={"folder": parent, "file_name": part, "is_folder": 1},
				pluck="name",
				limit_page_length=1,
			)
			if not match:
				return None
			frappe_folder = f"{parent}/{part}"

		last = parts[-1]

		if is_os_metadata(last):
			if not _can_read_folder(frappe_folder):
				return None
			if os_file_store.contains(path):
				return _MemoryFile(path, environ, last, frappe_folder)
			return None

		folder_match = frappe.get_list(
			"File",
			filters={"folder": frappe_folder, "file_name": last, "is_folder": 1},
			pluck="name",
			limit_page_length=1,
		)
		if folder_match:
			child_folder = f"{frappe_folder}/{last}"
			canonical = path if path.endswith("/") else path + "/"
			return FrappeCollection(canonical, environ, child_folder)

		file_rows = frappe.get_list(
			"File",
			filters={"folder": frappe_folder, "file_name": last, "is_folder": 0},
			fields=FILE_FIELDS,
			limit_page_length=1,
		)
		if file_rows:
			return FrappeFile(path, environ, file_rows[0])

		return None

	def is_readonly(self) -> bool:
		return False
