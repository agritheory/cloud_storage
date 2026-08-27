# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt
# WebDAV-specific S3 path strategy and upload/version pipeline.

import mimetypes
from urllib.parse import quote

import frappe
from boto3.exceptions import S3UploadFailedError
from frappe.core.doctype.file.file import File
from frappe.core.doctype.file.utils import get_content_hash

from cloud_storage.cloud_storage.overrides.file import (
	FILE_URL,
	get_cloud_storage_client,
)


WEBDAV_PREFIX = "webdav"


def is_cloud_storage_enabled() -> bool:
	config = frappe.conf.get("cloud_storage_settings", {})
	return bool(config and not config.get("use_local"))


def write_local_file(doc, content: bytes, content_hash: str | None = None) -> None:
	# save_file_on_filesystem reads _content (not content) and rejects a pre-set file_url.
	doc.file_url = None
	doc._content = content
	doc.save_file_on_filesystem()
	doc.db_set("file_url", doc.file_url)
	doc.db_set("file_size", len(content))
	if content_hash:
		doc.db_set("content_hash", content_hash)


def default_webdav_path(file: File, folder: str | None) -> str:
	"""Per-doc S3 key with a URL-safe filename component."""
	parts = [folder, WEBDAV_PREFIX, file.name, quote(file.file_name, safe="")]
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
	return default_webdav_path(file, folder)


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


def content_type_for(file_doc: File, fallback_name: str | None = None) -> str:
	return (
		getattr(file_doc, "content_type", None)
		or mimetypes.guess_type(fallback_name or file_doc.file_name or "")[0]
		or "application/octet-stream"
	)


def replace_existing_via_webdav(existing_doc: File, source_doc: File) -> File:
	"""Replace an existing WebDAV File doc with source content, preserving versions."""
	content_type = content_type_for(source_doc, existing_doc.file_name)
	source_size = source_doc.file_size or 0
	source_hash = source_doc.content_hash

	existing_doc.flags.cloud_storage = True
	existing_doc.content_type = content_type
	existing_doc.file_size = source_size
	if source_hash:
		existing_doc.content_hash = source_hash

	if not is_cloud_storage_enabled():
		content = source_doc.get_content()
		if isinstance(content, str):
			content = content.encode()
		source_hash = source_hash or get_content_hash(content)

		existing_doc.content_hash = source_hash
		write_local_file(existing_doc, content, source_hash)
		existing_doc.add_file_version(source_hash)
		return existing_doc

	client = get_cloud_storage_client()
	dest_path = get_webdav_path(existing_doc, client.folder)
	source_key = source_doc.s3_key
	if not source_key:
		frappe.throw("Source WebDAV file has no cloud storage key.")

	try:
		response = client.copy_object(
			Bucket=client.bucket,
			CopySource={"Bucket": client.bucket, "Key": source_key},
			Key=dest_path,
			ContentType=content_type,
			MetadataDirective="REPLACE",
		)
		version_id = response.get("VersionId") or source_hash
	except S3UploadFailedError:
		frappe.throw("File Upload Failed. Please try again.")
	except Exception as e:
		frappe.log_error("WebDAV replace error", e)
		raise

	existing_doc.db_set("file_url", FILE_URL.format(path=dest_path))
	existing_doc.db_set("s3_key", dest_path)
	existing_doc.db_set("file_size", source_size)
	if source_hash:
		existing_doc.db_set("content_hash", source_hash)
	if version_id:
		existing_doc.add_file_version(version_id)
	return existing_doc
