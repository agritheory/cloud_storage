# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

from __future__ import annotations

import io
import mimetypes
from typing import Optional

import frappe
from wsgidav.dav_error import DAVError, HTTP_FORBIDDEN, HTTP_NOT_FOUND
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

		return names

	def get_member(self, name: str):
		child_path = self.path.rstrip("/") + "/" + name
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
		raise DAVError(HTTP_FORBIDDEN)

	def create_collection(self, name: str):
		raise DAVError(HTTP_FORBIDDEN)

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
		raise DAVError(HTTP_FORBIDDEN)

	def support_recursive_delete(self) -> bool:
		return False

	def support_recursive_move(self, dest_path: str) -> bool:
		return False


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


class FrappeFile(DAVNonCollection):
	"""A Frappe File record exposed as a WebDAV resource."""

	def __init__(self, path: str, environ: dict, file_doc: dict) -> None:
		super().__init__(path, environ)
		self.file_doc = file_doc

	def get_content_length(self) -> Optional[int]:
		return self.file_doc.file_size or None

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
		return bool(self.file_doc.file_size)

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

		if self.file_doc.is_private:
			file_path = frappe.get_site_path("private", "files", self.file_doc.file_name)
		else:
			file_path = frappe.get_site_path("public", "files", self.file_doc.file_name)
		return open(file_path, "rb")

	def begin_write(self, content_type: Optional[str] = None):
		raise DAVError(HTTP_FORBIDDEN)

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
		raise DAVError(HTTP_FORBIDDEN)

	def support_recursive_delete(self) -> bool:
		return False

	def support_recursive_move(self, dest_path: str) -> bool:
		return False


class FrappeDAVProvider(DAVProvider):
	"""Root WebDAV provider: maps URL paths to Frappe File doctypes."""

	def get_resource_inst(self, path: str, environ: dict):
		"""Return the DAVCollection or DAVNonCollection for the given path, or None."""
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
