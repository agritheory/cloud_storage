# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import os
from unittest.mock import patch

import frappe
import pytest

from cloud_storage.cloud_storage.local_cache import (
	get_cached_bytes_total,
	get_connection,
	get_unevictable_bytes_total,
	iso,
)
from cloud_storage.cloud_storage.tasks import evict_lru_cache

from cloud_storage.tests.local_cache.helpers import create_attached_upload, get_cache, override_cache_settings, set_cache_fields


def test_eviction_removes_oldest_replicated_files_until_under_budget(mocked_s3_client):
	# neutralize stray replicated rows from other tests so "oldest" is unambiguous
	get_connection().execute(
		"UPDATE local_file_cache SET evicted=1, evicted_at=? WHERE evicted=0 AND replicated=1",
		(iso(frappe.utils.now_datetime()),),
	)

	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		baseline = get_cached_bytes_total()
		old_file = create_attached_upload(b"O" * 200, file_name="evict_old.bin")
		new_file = create_attached_upload(b"N" * 200, file_name="evict_new.bin")

	old_cache = get_cache(old_file.name)
	set_cache_fields(old_cache, replicated=1, accessed_at="2019-01-01 00:00:00")

	new_cache = get_cache(new_file.name)
	set_cache_fields(new_cache, replicated=1, accessed_at=frappe.utils.now_datetime())

	budget_bytes = baseline + new_cache.file_size + 10
	with override_cache_settings(max_cache_size_gb=budget_bytes / 1024**3):
		evict_lru_cache()

	old_cache = get_cache(old_file.name)
	new_cache = get_cache(new_file.name)
	assert old_cache.evicted == 1
	assert old_cache.evicted_at
	assert not os.path.exists(old_cache.local_path)
	assert new_cache.evicted == 0
	assert os.path.exists(new_cache.local_path)


def test_eviction_respects_retention_floor(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		baseline = get_cached_bytes_total()
		file = create_attached_upload(b"R" * 200, file_name="retention_floor.bin")

	cache = get_cache(file.name)
	set_cache_fields(cache, replicated=1, accessed_at=frappe.utils.now_datetime())

	with override_cache_settings(max_cache_size_gb=baseline / 1024**3, cache_retention_minutes=60):
		evict_lru_cache()

	cache = get_cache(file.name)
	assert cache.evicted == 0
	assert os.path.exists(cache.local_path)


def test_eviction_never_touches_unreplicated_files(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		baseline = get_cached_bytes_total()
		file = create_attached_upload(b"U" * 200, file_name="unreplicated.bin")

	cache = get_cache(file.name)
	assert cache.replicated == 0
	set_cache_fields(cache, accessed_at="2019-01-01 00:00:00")

	budget = baseline / 1024**3
	with override_cache_settings(max_cache_size_gb=budget, emergency_cache_size_gb=budget):
		evict_lru_cache()

	cache = get_cache(file.name)
	assert cache.evicted == 0
	assert os.path.exists(cache.local_path)


def test_emergency_ceiling_ignores_retention_floor(mocked_s3_client):
	# clear other replicated rows so eviction's "oldest" is unambiguous
	get_connection().execute(
		"UPDATE local_file_cache SET evicted=1, evicted_at=? WHERE evicted=0 AND replicated=1",
		(iso(frappe.utils.now_datetime()),),
	)

	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		replicated_file = create_attached_upload(b"E" * 200, file_name="emergency_replicated.bin")
		unreplicated_file = create_attached_upload(b"F" * 200, file_name="emergency_unreplicated.bin")

	replicated_cache = get_cache(replicated_file.name)
	set_cache_fields(replicated_cache, replicated=1, accessed_at=frappe.utils.now_datetime())

	unreplicated_cache = get_cache(unreplicated_file.name)
	set_cache_fields(unreplicated_cache, accessed_at=frappe.utils.now_datetime())

	emergency_budget = get_cached_bytes_total() - replicated_cache.file_size + 10
	with override_cache_settings(
		max_cache_size_gb=1, emergency_cache_size_gb=emergency_budget / 1024**3
	):
		evict_lru_cache()

	replicated_cache = get_cache(replicated_file.name)
	unreplicated_cache = get_cache(unreplicated_file.name)
	assert replicated_cache.evicted == 1
	assert unreplicated_cache.evicted == 0


def test_upload_rejected_when_emergency_ceiling_unrecoverable(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		create_attached_upload(b"B" * 500, file_name="ceiling_blocker.bin")
		unevictable = get_unevictable_bytes_total()

		with override_cache_settings(emergency_cache_size_gb=(unevictable - 1) / 1024**3):
			with pytest.raises(frappe.ValidationError):
				create_attached_upload(b"H" * 10, file_name="ceiling_rejected.bin")

	assert not frappe.db.exists("File", {"file_name": "ceiling_rejected.bin"})
	assert not get_connection().execute(
		"SELECT id FROM local_file_cache WHERE s3_key LIKE '%ceiling_rejected%'"
	).fetchone()


def test_oversized_single_file_admitted(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		baseline = get_cached_bytes_total()
		content = b"X" * 5000
		with override_cache_settings(max_cache_size_gb=baseline / 1024**3):
			file = create_attached_upload(content, file_name="oversized.bin")

	cache = get_cache(file.name)
	assert cache.file_size == len(content)
	assert os.path.exists(cache.local_path)

	with override_cache_settings(max_cache_size_gb=baseline / 1024**3):
		evict_lru_cache()

	cache = get_cache(file.name)
	assert cache.evicted == 0


def test_emergency_ceiling_accounts_for_incoming_file_size(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		create_attached_upload(b"C" * 500, file_name="ceiling_incoming_blocker.bin")
		unevictable = get_unevictable_bytes_total()

		# ceiling sits comfortably above the current unevictable total alone, but not once the
		# incoming file's own size is added - the old check only looked at the former
		with override_cache_settings(emergency_cache_size_gb=(unevictable + 5) / 1024**3):
			with pytest.raises(frappe.ValidationError):
				create_attached_upload(b"D" * 10, file_name="ceiling_incoming_rejected.bin")

	assert not frappe.db.exists("File", {"file_name": "ceiling_incoming_rejected.bin"})
	assert not get_connection().execute(
		"SELECT id FROM local_file_cache WHERE s3_key LIKE '%ceiling_incoming_rejected%'"
	).fetchone()
