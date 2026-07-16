# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import os
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import frappe
import pytest
from werkzeug.datastructures import FileMultiDict

from cloud_storage.cloud_storage.overrides.file import CloudStorageFile, retrieve


@pytest.fixture(scope="module", autouse=True)
def enable_local_cache(monkeymodule, patch_frappe_conf):
	"""Module-scoped overlay: enable the local cache with a tiny byte budget."""
	monkeymodule.setattr(
		"frappe.conf.cloud_storage_settings",
		{
			**frappe.conf.cloud_storage_settings,
			"local_cache_enabled": True,
			"max_cache_size_gb": 0.000005,
			"emergency_cache_size_gb": 0.00001,
			"cache_retention_minutes": 60,
			"warm_on_read": True,
		},
	)


@pytest.fixture(scope="module")
def restricted_user():
	email = "cache_read_test_user@example.com"
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Cache Read Test",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
	return email


@pytest.fixture
def example_bytes():
	# Each test appends a unique marker to this before uploading, so content_hash
	# never collides across tests and trips write_file()'s existing hash dedup.
	return (Path(__file__).parent / "fixtures" / "aticonrusthex.png").read_bytes()


def create_upload_file(content: bytes, is_private: bool = False, file_name: str = "file.png") -> CloudStorageFile:
	"""Upload a standalone file (no attached_to_doctype) as Administrator, so
	File.has_permission falls back purely to the owner/share checks."""
	f = BytesIO(content)

	files = FileMultiDict()
	files.add_file("file", f, file_name)

	frappe.set_user("Administrator")
	frappe.local.request = frappe._dict()
	frappe.local.request.method = "POST"
	frappe.local.request.files = files
	frappe.local.form_dict = frappe._dict()
	frappe.local.form_dict.is_private = is_private
	frappe.local.form_dict.doctype = None
	frappe.local.form_dict.docname = None
	frappe.local.form_dict.fieldname = None
	frappe.local.form_dict.file_url = None
	frappe.local.form_dict.folder = "Home"
	frappe.local.form_dict.file_name = file_name
	frappe.local.form_dict.optimize = False
	return frappe.call("frappe.handler.upload_file")


def reload_file(file: CloudStorageFile) -> CloudStorageFile:
	# The doc returned by create_upload_file() still holds the raw bytes in memory
	# (File.get_content returns those directly), so it never exercises the
	# cache/object-storage branches. Reload to get what a later read would see.
	return frappe.get_doc("File", file.name)


def test_warm_on_read_populates_cache(mocked_s3_client, example_bytes):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = example_bytes + b"warm_on_read"
		file = create_upload_file(content, file_name="warm_on_read.png")

		assert not frappe.db.exists("Local File Cache", {"file": file.name})

		fetched = reload_file(file).get_content()

		assert bytes(fetched) == content
		cache_name = frappe.db.exists("Local File Cache", {"file": file.name})
		assert cache_name
		cache = frappe.get_doc("Local File Cache", cache_name)
		assert cache.replicated == 1
		assert cache.file_size == len(content)
		assert Path(cache.local_path).read_bytes() == content


def test_retrieve_serves_cached_bytes_without_redirect(mocked_s3_client, example_bytes):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = example_bytes + b"retrieve_cached"
		file = create_upload_file(content, file_name="retrieve_cached.png")

		reload_file(file).get_content()
		cache_name = frappe.db.exists("Local File Cache", {"file": file.name})
		frappe.db.set_value("Local File Cache", cache_name, "accessed_at", "2020-01-01 00:00:00")

		frappe.local.response = frappe._dict()
		retrieve(file.s3_key)

		assert frappe.local.response.get("type") == "download"
		assert bytes(frappe.local.response.get("filecontent")) == content
		accessed_at = frappe.db.get_value("Local File Cache", cache_name, "accessed_at")
		assert str(accessed_at) > "2020-01-01 00:00:00"


def test_retrieve_uncached_redirects_to_presigned_url(mocked_s3_client, example_bytes):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = example_bytes + b"retrieve_uncached"
		file = create_upload_file(content, file_name="retrieve_uncached.png")

	assert not frappe.db.exists("Local File Cache", {"file": file.name})

	# Unpatched: needs the real client's bound get_presigned_url, which only
	# signs a URL locally, so no S3 mocking is required here.
	frappe.local.response = frappe._dict()
	retrieve(file.s3_key)

	assert frappe.local.response.get("type") == "redirect"
	assert frappe.local.response.get("location")


def test_private_file_local_serve_enforces_permission(mocked_s3_client, restricted_user, example_bytes):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = example_bytes + b"private_cached"
		file = create_upload_file(content, is_private=True, file_name="private_cached.png")
		reload_file(file).get_content()  # warms the cache as Administrator (owner)

		try:
			frappe.set_user(restricted_user)
			frappe.local.response = frappe._dict()
			with pytest.raises(frappe.PermissionError):
				retrieve(file.s3_key)

			frappe.set_user("Administrator")
			frappe.local.response = frappe._dict()
			retrieve(file.s3_key)
			assert frappe.local.response.get("type") == "download"
		finally:
			frappe.set_user("Administrator")


def test_warm_on_read_readmits_evicted_cache_row(mocked_s3_client, example_bytes):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = example_bytes + b"readmit_evicted"
		file = create_upload_file(content, file_name="readmit_evicted.png")
		reload_file(file).get_content()

		cache_name = frappe.db.exists("Local File Cache", {"file": file.name})
		cache = frappe.get_doc("Local File Cache", cache_name)
		os.remove(cache.local_path)
		cache.evicted = 1
		cache.evicted_at = frappe.utils.now_datetime()
		cache.save(ignore_permissions=True)

		fetched = reload_file(file).get_content()

		assert bytes(fetched) == content
		cache.reload()
		assert cache.evicted == 0
		assert cache.evicted_at is None
		assert Path(cache.local_path).read_bytes() == content


def test_public_file_local_serve_allows_guest(mocked_s3_client, example_bytes):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = example_bytes + b"public_cached"
		file = create_upload_file(content, is_private=False, file_name="public_cached.png")
		reload_file(file).get_content()  # warms the cache as Administrator

		try:
			frappe.set_user("Guest")
			frappe.local.response = frappe._dict()
			retrieve(file.s3_key)

			assert frappe.local.response.get("type") == "download"
			assert bytes(frappe.local.response.get("filecontent")) == content
		finally:
			frappe.set_user("Administrator")
