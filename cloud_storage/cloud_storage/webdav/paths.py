# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt
# WebDAV-specific S3 path strategy and upload pipeline.

import frappe
from boto3.exceptions import S3UploadFailedError
from frappe.core.doctype.file.file import File

from cloud_storage.cloud_storage.overrides.file import (
	FILE_URL,
	get_cloud_storage_client,
)


_WEBDAV_PREFIX = "webdav"


def _default_webdav_path(file: File, folder: str | None) -> str:
	"""Per-doc S3 key. ``#`` is %-escaped to match legacy behaviour."""
	parts = [folder, _WEBDAV_PREFIX, file.name, file.file_name.replace("#", "%23")]
	return "/".join(p for p in parts if p)


def get_webdav_path(file: File, folder: str | None) -> str:
	"""S3 key for a WebDAV file. Checks cloud_storage_webdav_path_generator first."""
	hooks = frappe.get_hooks("cloud_storage_webdav_path_generator")
	if hooks:
		try:
			return frappe.get_attr(hooks[0])(file, folder)
		except Exception as e:
			frappe.log_error(
				f"cloud_storage_webdav_path_generator failed: {e}",
				"WebDAV Path Generator Error",
			)
	return _default_webdav_path(file, folder)


def upload_via_webdav(file_doc: File, content: bytes, content_type: str) -> File:
	"""Upload content to S3 using the WebDAV-specific path (avoids name collision)."""
	client = get_cloud_storage_client()
	path = get_webdav_path(file_doc, client.folder)
	file_doc.db_set("file_url", FILE_URL.format(path=path))

	version_id = None
	try:
		response = client.put_object(
			Body=content,
			Bucket=client.bucket,
			Key=path,
			ContentType=content_type,
		)
		version_id = response.get("VersionId") or file_doc.content_hash
		file_doc.associate_files(file_doc.attached_to_doctype, file_doc.attached_to_name)
	except S3UploadFailedError:
		frappe.throw("File Upload Failed. Please try again.")
	except Exception as e:
		frappe.log_error("WebDAV upload error", e)
		raise

	if version_id:
		file_doc.add_file_version(version_id)
	file_doc.db_set("s3_key", path)
	file_doc.db_set("file_size", len(content))
	if file_doc.content_hash:
		file_doc.db_set("content_hash", file_doc.content_hash)
	return file_doc
