# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import os
from unittest.mock import patch

import frappe
import pytest
from botocore.exceptions import ClientError

from cloud_storage.cloud_storage.local_cache import get_cached_bytes_total, get_connection, get_health, update_health
from cloud_storage.cloud_storage.tasks import (
	check_cloud_health,
	process_pending_deletes,
	reconcile_local_cache,
	replicate_cached_file,
	retry_pending_replications,
)

from cloud_storage.tests.local_cache.helpers import (
	create_attached_upload,
	create_upload_file,
	degraded_cloud_storage,
	down_endpoint_error,
	get_cache,
	override_cache_settings,
	set_cache_fields,
)


def test_recovery_drains_backlog_oldest_first(mocked_s3_client):
	# neutralize stray unreplicated rows from other tests so the sweep below is unambiguous
	get_connection().execute(
		"UPDATE local_file_cache SET replicated=1 WHERE replicated=0 AND pending_delete=0"
	)

	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	), patch.object(mocked_s3_client, "put_object", side_effect=down_endpoint_error()):
		older = create_attached_upload(b"recovery drain older", file_name="recovery_older.bin")
		newer = create_attached_upload(b"recovery drain newer", file_name="recovery_newer.bin")

	older_cache = get_cache(older.name)
	newer_cache = get_cache(newer.name)
	assert older_cache.replicated == 0
	assert newer_cache.replicated == 0

	# pin creation order explicitly rather than relying on insert timing precision
	set_cache_fields(older_cache, creation="2000-01-01 00:00:00")
	set_cache_fields(newer_cache, creation="2000-01-01 00:00:01")

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	), patch(
		"cloud_storage.cloud_storage.tasks.replicate_cached_file", wraps=replicate_cached_file
	) as spy:
		retry_pending_replications()

	assert [call.args[0] for call in spy.call_args_list] == [older.name, newer.name]

	older_cache = get_cache(older.name)
	newer_cache = get_cache(newer.name)
	assert older_cache.replicated == 1
	assert newer_cache.replicated == 1
	assert mocked_s3_client.head_object(Bucket=mocked_s3_client.bucket, Key=older_cache.s3_key)
	assert mocked_s3_client.head_object(Bucket=mocked_s3_client.bucket, Key=newer_cache.s3_key)


def test_recovery_processes_tombstones(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = b"recovery processes tombstones"
		file = create_upload_file(content, file_name="recovery_tombstone.bin")

	cache = get_cache(file.name)
	s3_key = cache.s3_key

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	):
		replicate_cached_file(file.name)

	assert mocked_s3_client.head_object(Bucket=mocked_s3_client.bucket, Key=s3_key)

	with degraded_cloud_storage(), patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		frappe.delete_doc("File", file.name, ignore_permissions=True)

	cache = get_cache(file.name)
	assert cache.pending_delete == 1

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	):
		process_pending_deletes()

	assert not get_cache(file.name)
	with pytest.raises(ClientError):
		mocked_s3_client.head_object(Bucket=mocked_s3_client.bucket, Key=s3_key)


def test_replication_retries_stop_at_max_retries(mocked_s3_client):
	# neutralize stray unreplicated rows from other tests, same as the backlog-order test above
	get_connection().execute(
		"UPDATE local_file_cache SET replicated=1 WHERE replicated=0 AND pending_delete=0"
	)

	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	), patch.object(mocked_s3_client, "put_object", side_effect=down_endpoint_error()):
		below_cap = create_attached_upload(b"below cap retry", file_name="retry_below_cap.bin")
		at_cap = create_attached_upload(b"at cap retry", file_name="retry_at_cap.bin")

	below_cap_cache = get_cache(below_cap.name)
	at_cap_cache = get_cache(at_cap.name)
	set_cache_fields(below_cap_cache, replication_attempts=2)
	set_cache_fields(at_cap_cache, replication_attempts=3)

	with override_cache_settings(replication_max_retries=3), patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	), patch(
		"cloud_storage.cloud_storage.tasks.replicate_cached_file", wraps=replicate_cached_file
	) as spy:
		retry_pending_replications()

	called_names = [call.args[0] for call in spy.call_args_list]
	assert below_cap.name in called_names
	assert at_cap.name not in called_names

	below_cap_cache = get_cache(below_cap.name)
	at_cap_cache = get_cache(at_cap.name)
	assert below_cap_cache.replicated == 1
	assert at_cap_cache.replicated == 0
	assert at_cap_cache.replication_attempts == 3


def test_recovery_resets_replication_attempts_and_error(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	), patch.object(mocked_s3_client, "put_object", side_effect=down_endpoint_error()):
		content = b"recovery resets attempts"
		file = create_attached_upload(content, file_name="recovery_reset_attempts.bin")

	cache = get_cache(file.name)
	# simulate a long outage: attempts already past any reasonable replication_max_retries
	set_cache_fields(
		cache, replication_attempts=25, last_replication_error="stale error from before the outage ended"
	)
	update_health(
		{"status": "Degraded", "consecutive_failures": 5, "degraded_since": frappe.utils.now_datetime()}
	)

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	):
		check_cloud_health()

	assert get_health().status == "Healthy"

	cache = get_cache(file.name)
	assert cache.replication_attempts == 0
	assert not cache.last_replication_error

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	):
		retry_pending_replications()

	cache = get_cache(file.name)
	assert cache.replicated == 1
	assert not cache.last_replication_error
	assert mocked_s3_client.head_object(Bucket=mocked_s3_client.bucket, Key=cache.s3_key)


def test_check_cloud_health_drains_backlog_immediately_on_recovery(mocked_s3_client):
	get_connection().execute(
		"UPDATE local_file_cache SET replicated=1 WHERE replicated=0 AND pending_delete=0"
	)

	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	), patch.object(mocked_s3_client, "put_object", side_effect=down_endpoint_error()):
		file = create_attached_upload(
			b"drain immediately on recovery", file_name="drain_immediately.bin"
		)

	cache = get_cache(file.name)
	assert cache.replicated == 0

	update_health(
		{"status": "Degraded", "consecutive_failures": 5, "degraded_since": frappe.utils.now_datetime()}
	)

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	):
		check_cloud_health()

	assert get_health().status == "Healthy"
	cache = get_cache(file.name)
	assert cache.replicated == 1


def test_reconciliation_marks_missing_files_evicted(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = b"reconciliation marks missing evicted"
		file = create_attached_upload(content, file_name="reconcile_missing.bin")

	cache = get_cache(file.name)
	os.remove(cache.local_path)

	reconcile_local_cache()

	cache = get_cache(file.name)
	assert cache.evicted == 1
	assert cache.evicted_at


def test_reconciliation_removes_orphaned_files(mocked_s3_client):
	baseline = get_cached_bytes_total()

	orphan_dir = frappe.get_site_path("local_cache", "zz")
	os.makedirs(orphan_dir, exist_ok=True)
	orphan_path = os.path.join(orphan_dir, "reconcile_orphan_scratch")
	with open(orphan_path, "wb") as fh:
		fh.write(b"orphan bytes")

	reconcile_local_cache()

	assert not os.path.exists(orphan_path)
	assert get_cached_bytes_total() == baseline


def test_reconciliation_survives_differently_spelled_local_path(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = b"reconciliation survives differently spelled path"
		file = create_attached_upload(content, file_name="reconcile_dotted_path.bin")

	cache = get_cache(file.name)
	real_path = cache.local_path
	directory, filename = os.path.split(real_path)
	differently_spelled_path = os.path.join(directory, ".", filename)
	set_cache_fields(cache, local_path=differently_spelled_path)

	reconcile_local_cache()

	cache = get_cache(file.name)
	assert cache.evicted == 0
	assert os.path.exists(real_path)
