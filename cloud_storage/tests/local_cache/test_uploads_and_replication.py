# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import os
from pathlib import Path
from unittest.mock import patch

import frappe
import pytest
from botocore.exceptions import ClientError

from cloud_storage.cloud_storage.local_cache import (
	admit_local_cache_record,
	delete_cache_record,
	get_connection,
	get_local_cache_path,
	write_local_cache_bytes,
)
from cloud_storage.cloud_storage.overrides.file import validate_config
from cloud_storage.cloud_storage.tasks import replicate_cached_file
from cloud_storage.migration import migrate_files

from cloud_storage.tests.local_cache.helpers import (
	create_attached_upload,
	create_local_only_file,
	create_uncached_cloud_file,
	failing_sql,
	get_cache,
	override_cache_settings,
)


def test_upload_creates_cache_record_and_local_bytes(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = b"upload creates cache record"
		file = create_attached_upload(content, file_name="upload_creates_cache.bin")

	cache = get_cache(file.name)
	assert cache.replicated == 0
	assert cache.file_size == len(content)
	assert Path(cache.local_path).read_bytes() == content
	assert file.s3_key
	assert file.file_url


def test_duplicate_content_shares_cache_entry(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = b"duplicate content shared cache entry"
		file1 = create_attached_upload(content, file_name="dup1.bin")
		file2 = create_attached_upload(content, file_name="dup2.bin")

	# file2 gets merged into file1 in after_insert(); its own name stops existing.
	assert not frappe.db.exists("File", file2.name)
	assert frappe.db.exists("File", file1.name)
	rows = (
		get_connection()
		.execute("SELECT id FROM local_file_cache WHERE content_hash = ?", (file1.content_hash,))
		.fetchall()
	)
	assert len(rows) == 1


def test_shared_local_path_survives_delete_of_one_reference(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = b"same bytes referenced by two separate cache rows"
		file1 = create_attached_upload(content, file_name="shared1.bin")

	cache1 = get_cache(file1.name)
	shared_path = cache1.local_path

	conn = get_connection()
	conn.execute(
		"INSERT INTO local_file_cache (file, local_path, file_size, s3_key, content_hash, accessed_at, creation) "
		"VALUES (?, ?, ?, ?, ?, ?, ?)",
		(
			"shared-reference-fixture",
			shared_path,
			cache1.file_size,
			"some/other/key",
			cache1.content_hash,
			"2026-01-01 00:00:00",
			"2026-01-01 00:00:00",
		),
	)

	try:
		delete_cache_record(file1.name)

		assert os.path.exists(shared_path)
		assert get_cache("shared-reference-fixture")
	finally:
		conn.execute("DELETE FROM local_file_cache WHERE file = ?", ("shared-reference-fixture",))
		if os.path.exists(shared_path):
			os.remove(shared_path)


def test_admission_failure_after_commit_logs_error(mocked_s3_client):
	before = frappe.db.count("Error Log")

	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	), failing_sql("INSERT INTO local_file_cache"):
		with pytest.raises(Exception, match="boom"):
			create_attached_upload(
				b"admission fails after commit", file_name="admission_after_commit_fail.bin"
			)

	after = frappe.db.count("Error Log")
	assert after > before


def test_cache_disabled_preserves_current_behavior(mocked_s3_client):
	with override_cache_settings(local_cache_enabled=False), patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = b"cache disabled synchronous upload"
		file = create_attached_upload(content, file_name="cache_disabled.bin")

	assert not get_cache(file.name)
	head = mocked_s3_client.head_object(Bucket=mocked_s3_client.bucket, Key=file.s3_key)
	assert head["ContentLength"] == len(content)


def test_replication_uploads_and_marks_durable(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = b"replication marks durable"
		file = create_attached_upload(content, file_name="replicate_ok.bin")

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	):
		replicate_cached_file(file.name)

	cache = get_cache(file.name)
	assert cache.replicated == 1
	assert cache.replicated_at
	head = mocked_s3_client.head_object(Bucket=mocked_s3_client.bucket, Key=cache.s3_key)
	assert head["ContentLength"] == len(content)
	assert frappe.get_all("File Version", filters={"parent": file.name})


def test_replication_failure_increments_attempts(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = b"replication failure keeps bytes"
		file = create_attached_upload(content, file_name="replicate_fail.bin")

	local_path = get_cache(file.name).local_path

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	), patch.object(mocked_s3_client, "put_object", side_effect=Exception("boom")):
		replicate_cached_file(file.name)

	cache = get_cache(file.name)
	assert cache.replicated == 0
	assert cache.replication_attempts == 1
	assert cache.last_replication_error
	assert not frappe.get_all("File Version", filters={"parent": file.name})
	assert Path(local_path).read_bytes() == content


def test_migration_does_not_populate_cache(mocked_s3_client):
	file = create_local_only_file(b"migration content", file_name="migrate_me.bin")

	before = get_connection().execute("SELECT COUNT(*) FROM local_file_cache").fetchone()[0]

	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	), patch("cloud_storage.migration.get_cloud_storage_client", return_value=mocked_s3_client):
		migrate_files(doctype="User")

	after = get_connection().execute("SELECT COUNT(*) FROM local_file_cache").fetchone()[0]
	assert after == before

	file.reload()
	assert file.s3_key
	head = mocked_s3_client.head_object(Bucket=mocked_s3_client.bucket, Key=file.s3_key)
	assert head["ContentLength"] > 0
	assert frappe.get_all("File Version", filters={"parent": file.name})


def test_bypass_flag_on_interactive_upload(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		file = create_uncached_cloud_file(b"bypass flag content", file_name="bypass_upload.bin")

	assert not get_cache(file.name)
	assert file.s3_key


def test_replication_enqueued_after_commit(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	), patch("frappe.enqueue") as mock_enqueue:
		create_attached_upload(
			b"enqueue after commit", file_name="enqueue_after_commit.bin", commit=False
		)
		mock_enqueue.assert_not_called()

		frappe.db.commit()
		mock_enqueue.assert_called_once()
		assert mock_enqueue.call_args.kwargs["enqueue_after_commit"] is True


def test_admit_cache_record_cleans_up_bytes_on_insert_failure(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		file = create_uncached_cloud_file(b"admit failure cleanup", file_name="admit_failure.bin")

	local_path = get_local_cache_path(file.content_hash)
	with open(local_path, "wb") as fh:
		fh.write(b"admit failure cleanup")

	with failing_sql("INSERT INTO local_file_cache"):
		with pytest.raises(Exception, match="boom"):
			admit_local_cache_record(file, local_path)

	assert not os.path.exists(local_path)


def test_reupload_preserves_previous_bytes_when_admission_fails(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content_v1 = b"content that must survive a failed reupload admission"
		file = create_attached_upload(content_v1, file_name="admission_failure_reupload.bin")

	cache = get_cache(file.name)
	local_path_v1 = cache.local_path
	assert os.path.exists(local_path_v1)

	file_doc = frappe.get_doc("File", file.name)
	file_doc.content = b"new content whose admission will fail"
	file_doc.content_hash = "admission-failure-v2-hash"
	new_local_path = write_local_cache_bytes(file_doc)

	with failing_sql("UPDATE local_file_cache SET local_path"):
		with pytest.raises(Exception, match="boom"):
			admit_local_cache_record(file_doc, new_local_path)

	assert not os.path.exists(new_local_path)
	assert os.path.exists(local_path_v1)
	assert Path(local_path_v1).read_bytes() == content_v1


def test_replication_deletes_object_when_row_and_file_vanish_mid_upload(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = b"orphaned by concurrent delete"
		file = create_attached_upload(content, file_name="orphan_race.bin")

	cache_id = get_cache(file.name).id
	s3_key = get_cache(file.name).s3_key
	file_name = file.name

	original_put_object = mocked_s3_client.put_object

	def put_then_vanish(*args, **kwargs):
		response = original_put_object(*args, **kwargs)
		get_connection().execute("DELETE FROM local_file_cache WHERE id = ?", (cache_id,))
		frappe.db.delete("File", {"name": file_name})
		return response

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	), patch.object(mocked_s3_client, "put_object", side_effect=put_then_vanish):
		replicate_cached_file(file_name)

	with pytest.raises(ClientError):
		mocked_s3_client.head_object(Bucket=mocked_s3_client.bucket, Key=s3_key)


def test_replication_race_reupload_full_flow(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content_v1 = b"original content before the concurrent reupload"
		file = create_attached_upload(content_v1, file_name="reupload_race_full.bin")

	s3_key = get_cache(file.name).s3_key
	content_v2 = b"replacement content uploaded while v1 was still replicating"

	original_put_object = mocked_s3_client.put_object

	def put_v1_then_reupload_v2(*args, **kwargs):
		response = original_put_object(*args, **kwargs)
		create_attached_upload(content_v2, file_name="reupload_race_full.bin")
		return response

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	), patch.object(mocked_s3_client, "put_object", side_effect=put_v1_then_reupload_v2):
		replicate_cached_file(file.name)

	cache = get_cache(file.name)
	assert cache.replicated == 0

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	):
		replicate_cached_file(file.name)

	cache = get_cache(file.name)
	assert cache.replicated == 1
	body = mocked_s3_client.get_object(Bucket=mocked_s3_client.bucket, Key=s3_key)["Body"].read()
	assert body == content_v2


def test_local_cache_enabled_rejects_use_local():
	with override_cache_settings(use_local=True, local_cache_enabled=True):
		with pytest.raises(frappe.ValidationError):
			validate_config()
