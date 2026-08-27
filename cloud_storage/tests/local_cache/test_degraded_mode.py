# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import os
from pathlib import Path
from unittest.mock import patch

import frappe
import pytest
from botocore.exceptions import ClientError

from cloud_storage.cloud_storage.local_cache import (
	get_cached_bytes_total,
	get_health,
	is_cloud_storage_degraded,
	update_health,
)
from cloud_storage.cloud_storage.overrides.file import retrieve
from cloud_storage.cloud_storage.tasks import evict_lru_cache, replicate_cached_file

from cloud_storage.tests.local_cache.helpers import (
	create_attached_upload,
	create_uncached_cloud_file,
	create_upload_file,
	degraded_cloud_storage,
	down_endpoint_error,
	get_cache,
	override_cache_settings,
	reload_file,
	set_cache_fields,
)


def test_upload_succeeds_while_object_store_down(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	), patch.object(mocked_s3_client, "put_object", side_effect=down_endpoint_error()):
		content = b"upload succeeds while object store down"
		file = create_attached_upload(content, file_name="degraded_upload.bin")

	cache = get_cache(file.name)
	assert cache.replicated == 0
	assert Path(cache.local_path).read_bytes() == content

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	), patch.object(mocked_s3_client, "put_object", side_effect=down_endpoint_error()):
		replicate_cached_file(file.name)

	cache = get_cache(file.name)
	assert cache.replicated == 0
	assert cache.replication_attempts == 1
	assert cache.last_replication_error


def test_cached_read_succeeds_while_object_store_down(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = b"cached read succeeds while object store down"
		file = create_upload_file(content, file_name="degraded_cached_read.bin")
		reload_file(file).get_content()

	cache = get_cache(file.name)

	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	), patch.object(mocked_s3_client, "get_object", side_effect=down_endpoint_error()):
		assert bytes(reload_file(file).get_content()) == content

		frappe.local.response = frappe._dict()
		retrieve(cache.s3_key)
		assert frappe.local.response.get("type") == "download"
		assert bytes(frappe.local.response.get("filecontent")) == content


def test_uncached_read_returns_503_not_redirect(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		file = create_uncached_cloud_file(b"uncached during outage", file_name="degraded_uncached.bin")

	assert not get_cache(file.name)

	with degraded_cloud_storage():
		frappe.local.response = frappe._dict()
		retrieve(file.s3_key)

	assert frappe.local.response.get("http_status_code") == 503
	assert frappe.local.response.get("type") != "redirect"
	assert not frappe.local.response.get("location")


def test_delete_creates_tombstone_while_down(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = b"delete creates tombstone while down"
		file = create_upload_file(content, file_name="degraded_delete.bin")

	cache = get_cache(file.name)
	local_path = cache.local_path
	s3_key = cache.s3_key
	assert os.path.exists(local_path)

	with degraded_cloud_storage(), patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		frappe.delete_doc("File", file.name, ignore_permissions=True)

	assert not frappe.db.exists("File", file.name)
	assert not os.path.exists(local_path)

	cache = get_cache(file.name)
	assert cache
	assert cache.pending_delete == 1
	assert cache.s3_key == s3_key


def test_eviction_paused_while_degraded(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		baseline = get_cached_bytes_total()
		file = create_attached_upload(b"P" * 200, file_name="degraded_eviction.bin")

	cache = get_cache(file.name)
	set_cache_fields(cache, replicated=1, accessed_at="2019-01-01 00:00:00")

	budget_bytes = baseline + 10
	with degraded_cloud_storage(), override_cache_settings(
		max_cache_size_gb=budget_bytes / 1024**3
	):
		evict_lru_cache()

	cache = get_cache(file.name)
	assert cache.evicted == 0
	assert os.path.exists(cache.local_path)


def test_delete_tombstones_on_transient_error_while_healthy(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = b"delete tombstones transient error while healthy"
		file = create_upload_file(content, file_name="delete_transient_healthy.bin")

	cache = get_cache(file.name)
	local_path = cache.local_path
	s3_key = cache.s3_key
	assert get_health().status != "Degraded"

	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	), patch.object(mocked_s3_client, "delete_object", side_effect=down_endpoint_error()):
		frappe.delete_doc("File", file.name, ignore_permissions=True)

	assert not frappe.db.exists("File", file.name)
	assert not os.path.exists(local_path)

	cache = get_cache(file.name)
	assert cache
	assert cache.pending_delete == 1
	assert cache.s3_key == s3_key


def test_delete_aborts_on_client_error_even_with_cache_enabled(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		file = create_upload_file(b"delete aborts on client error", file_name="delete_client_error.bin")

	client_error = ClientError(
		{"Error": {"Code": "AccessDenied", "Message": "Denied"}}, "DeleteObject"
	)
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	), patch.object(mocked_s3_client, "delete_object", side_effect=client_error):
		with pytest.raises(frappe.ValidationError):
			frappe.delete_doc("File", file.name, ignore_permissions=True)


def test_degraded_status_ignored_when_cache_disabled():
	update_health({"status": "Degraded"})
	try:
		with override_cache_settings(local_cache_enabled=False):
			assert is_cloud_storage_degraded() is False
	finally:
		update_health({"status": "Healthy"})


def test_retrieve_redirects_when_cache_disabled_even_if_status_stuck_degraded(mocked_s3_client):
	update_health({"status": "Degraded"})
	try:
		with patch(
			"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
			return_value=mocked_s3_client,
		):
			content = b"stuck degraded status ignored when cache disabled"
			file = create_uncached_cloud_file(content, file_name="stuck_degraded.bin")

		# unpatched: presigned URL signing needs no S3 mock
		with override_cache_settings(local_cache_enabled=False):
			frappe.local.response = frappe._dict()
			retrieve(file.s3_key)

		assert frappe.local.response.get("type") == "redirect"
		assert frappe.local.response.get("http_status_code") != 503
	finally:
		update_health({"status": "Healthy"})
