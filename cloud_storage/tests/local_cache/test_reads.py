# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import os
from pathlib import Path
from unittest.mock import patch

import frappe
import pytest

from cloud_storage.cloud_storage.local_cache import get_connection, iso
from cloud_storage.cloud_storage.overrides.file import retrieve

from cloud_storage.tests.local_cache.helpers import (
	create_uncached_cloud_file,
	create_upload_file,
	get_cache,
	override_cache_settings,
	reload_file,
)


def test_warm_on_read_populates_cache(mocked_s3_client, example_bytes):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = example_bytes + b"warm_on_read"
		file = create_uncached_cloud_file(content, file_name="warm_on_read.png")

		assert not get_cache(file.name)

		with override_cache_settings(max_cache_size_gb=1):
			fetched = reload_file(file).get_content()

		assert bytes(fetched) == content
		cache = get_cache(file.name)
		assert cache
		assert cache.replicated == 1
		assert cache.file_size == len(content)
		assert Path(cache.local_path).read_bytes() == content


def test_warm_on_read_skips_oversized_file(mocked_s3_client, example_bytes):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = example_bytes + b"warm_on_read_oversized"
		file = create_uncached_cloud_file(content, file_name="warm_on_read_oversized.png")

		# budget == content size, so the 5% admission threshold is far below it
		with override_cache_settings(max_cache_size_gb=len(content) / 1024**3):
			fetched = reload_file(file).get_content()

		assert bytes(fetched) == content
		assert not get_cache(file.name)


def test_retrieve_serves_cached_bytes_without_redirect(mocked_s3_client, example_bytes):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = example_bytes + b"retrieve_cached"
		file = create_upload_file(content, file_name="retrieve_cached.png")

		reload_file(file).get_content()
		cache = get_cache(file.name)
		get_connection().execute(
			"UPDATE local_file_cache SET accessed_at = ? WHERE id = ?", ("2020-01-01 00:00:00", cache.id)
		)

		frappe.local.response = frappe._dict()
		retrieve(file.s3_key)

		assert frappe.local.response.get("type") == "download"
		assert bytes(frappe.local.response.get("filecontent")) == content
		assert frappe.local.response.get("display_content_as") == "inline"
		accessed_at = get_cache(file.name).accessed_at
		assert str(accessed_at) > "2020-01-01 00:00:00"


def test_retrieve_uncached_redirects_to_presigned_url(mocked_s3_client, example_bytes):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = example_bytes + b"retrieve_uncached"
		file = create_uncached_cloud_file(content, file_name="retrieve_uncached.png")

	assert not get_cache(file.name)

	# unpatched: presigned URL signing needs no S3 mock
	frappe.local.response = frappe._dict()
	retrieve(file.s3_key)

	assert frappe.local.response.get("type") == "redirect"
	assert frappe.local.response.get("location")


def test_private_file_local_serve_enforces_permission(
	mocked_s3_client, restricted_user, example_bytes
):
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
		file = create_uncached_cloud_file(content, file_name="readmit_evicted.png")
		with override_cache_settings(max_cache_size_gb=1):
			reload_file(file).get_content()

		cache = get_cache(file.name)
		os.remove(cache.local_path)
		get_connection().execute(
			"UPDATE local_file_cache SET evicted=1, evicted_at=? WHERE id=?",
			(iso(frappe.utils.now_datetime()), cache.id),
		)

		with override_cache_settings(max_cache_size_gb=1):
			fetched = reload_file(file).get_content()

		assert bytes(fetched) == content
		cache = get_cache(file.name)
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
