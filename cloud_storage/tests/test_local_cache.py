# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import os
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import frappe
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from werkzeug.datastructures import FileMultiDict

from cloud_storage.cloud_storage.local_cache import (
	admit_local_cache_record,
	get_cached_bytes_total,
	get_local_cache_path,
	get_unevictable_bytes_total,
	is_cloud_storage_degraded,
)
from cloud_storage.cloud_storage.overrides.file import CloudStorageFile, retrieve, validate_config
from cloud_storage.cloud_storage.tasks import (
	check_cloud_health,
	evict_lru_cache,
	process_pending_deletes,
	reconcile_local_cache,
	replicate_cached_file,
	retry_pending_replications,
)
from cloud_storage.migration import migrate_files


@contextmanager
def override_cache_settings(**overrides):
	original = frappe.conf.cloud_storage_settings
	frappe.conf.cloud_storage_settings = {**original, **overrides}
	try:
		yield
	finally:
		frappe.conf.cloud_storage_settings = original


@contextmanager
def degraded_cloud_storage():
	frappe.db.set_single_value("Cloud Storage Health", "status", "Degraded")
	try:
		yield
	finally:
		frappe.db.set_single_value("Cloud Storage Health", "status", "Healthy")


def down_endpoint_error():
	return EndpointConnectionError(endpoint_url="https://down.example")


@pytest.fixture(scope="module", autouse=True)
def enable_local_cache(monkeymodule, patch_frappe_conf):
	# generous emergency_cache_size_gb: eviction tests below set their own tight budgets
	monkeymodule.setattr(
		"frappe.conf.cloud_storage_settings",
		{
			**frappe.conf.cloud_storage_settings,
			"local_cache_enabled": True,
			"max_cache_size_gb": 0.000005,
			"emergency_cache_size_gb": 1,
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
	# each test appends a unique marker before uploading, to avoid write_file's hash dedup
	return (Path(__file__).parent / "fixtures" / "aticonrusthex.png").read_bytes()


def create_upload_file(
	content: bytes, is_private: bool = False, file_name: str = "file.png"
) -> CloudStorageFile:
	# unattached: File.has_permission falls back to owner/share checks only
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
	# the returned doc still has raw bytes in memory, which get_content() prefers
	return frappe.get_doc("File", file.name)


def create_uncached_cloud_file(content: bytes, file_name: str) -> CloudStorageFile:
	# already in the bucket, no local cache row - for testing warm_on_read
	file = frappe.get_doc(
		{"doctype": "File", "file_name": file_name, "content": content, "is_private": 0}
	)
	file.flags.bypass_local_cache = True
	file.insert(ignore_permissions=True)
	return file


def create_attached_upload(content: bytes, file_name: str) -> CloudStorageFile:
	f = BytesIO(content)
	files = FileMultiDict()
	files.add_file("file", f, file_name)

	frappe.set_user("Administrator")
	frappe.local.request = frappe._dict()
	frappe.local.request.method = "POST"
	frappe.local.request.files = files
	frappe.local.form_dict = frappe._dict()
	frappe.local.form_dict.is_private = 0
	frappe.local.form_dict.doctype = "User"
	frappe.local.form_dict.docname = "Administrator"
	frappe.local.form_dict.fieldname = None
	frappe.local.form_dict.file_url = None
	frappe.local.form_dict.folder = "Home"
	frappe.local.form_dict.file_name = file_name
	frappe.local.form_dict.optimize = False
	return frappe.call("frappe.handler.upload_file")


def create_local_only_file(content: bytes, file_name: str) -> CloudStorageFile:
	old_settings = frappe.conf.cloud_storage_settings
	frappe.conf.cloud_storage_settings = None
	try:
		return create_attached_upload(content, file_name)
	finally:
		frappe.conf.cloud_storage_settings = old_settings


def get_cache(file_name: str):
	return frappe.get_doc(
		"Local File Cache", frappe.db.exists("Local File Cache", {"file": file_name})
	)


def test_warm_on_read_populates_cache(mocked_s3_client, example_bytes):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = example_bytes + b"warm_on_read"
		file = create_uncached_cloud_file(content, file_name="warm_on_read.png")

		assert not frappe.db.exists("Local File Cache", {"file": file.name})

		with override_cache_settings(max_cache_size_gb=1):
			fetched = reload_file(file).get_content()

		assert bytes(fetched) == content
		cache_name = frappe.db.exists("Local File Cache", {"file": file.name})
		assert cache_name
		cache = frappe.get_doc("Local File Cache", cache_name)
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
		assert not frappe.db.exists("Local File Cache", {"file": file.name})


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
		assert frappe.local.response.get("display_content_as") == "inline"
		accessed_at = frappe.db.get_value("Local File Cache", cache_name, "accessed_at")
		assert str(accessed_at) > "2020-01-01 00:00:00"


def test_retrieve_uncached_redirects_to_presigned_url(mocked_s3_client, example_bytes):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = example_bytes + b"retrieve_uncached"
		file = create_uncached_cloud_file(content, file_name="retrieve_uncached.png")

	assert not frappe.db.exists("Local File Cache", {"file": file.name})

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

		cache_name = frappe.db.exists("Local File Cache", {"file": file.name})
		cache = frappe.get_doc("Local File Cache", cache_name)
		os.remove(cache.local_path)
		cache.evicted = 1
		cache.evicted_at = frappe.utils.now_datetime()
		cache.save(ignore_permissions=True)

		with override_cache_settings(max_cache_size_gb=1):
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
	rows = frappe.get_all("Local File Cache", filters={"content_hash": file1.content_hash})
	assert len(rows) == 1


def test_cache_disabled_preserves_current_behavior(mocked_s3_client):
	with override_cache_settings(local_cache_enabled=False), patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = b"cache disabled synchronous upload"
		file = create_attached_upload(content, file_name="cache_disabled.bin")

	assert not frappe.db.exists("Local File Cache", {"file": file.name})
	head = mocked_s3_client.head_object(Bucket=mocked_s3_client.bucket, Key=file.s3_key)
	assert head["ContentLength"] == len(content)


def test_replication_uploads_and_marks_durable(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = b"replication marks durable"
		file = create_attached_upload(content, file_name="replicate_ok.bin")

	cache_name = get_cache(file.name).name

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	):
		replicate_cached_file(cache_name)

	cache = frappe.get_doc("Local File Cache", cache_name)
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

	cache_name = get_cache(file.name).name
	local_path = get_cache(file.name).local_path

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	), patch.object(mocked_s3_client, "put_object", side_effect=Exception("boom")):
		replicate_cached_file(cache_name)

	cache = frappe.get_doc("Local File Cache", cache_name)
	assert cache.replicated == 0
	assert cache.replication_attempts == 1
	assert cache.last_replication_error
	assert not frappe.get_all("File Version", filters={"parent": file.name})
	assert Path(local_path).read_bytes() == content


def test_eviction_removes_oldest_replicated_files_until_under_budget(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		baseline = get_cached_bytes_total()
		old_file = create_attached_upload(b"O" * 200, file_name="evict_old.bin")
		new_file = create_attached_upload(b"N" * 200, file_name="evict_new.bin")

	old_cache = get_cache(old_file.name)
	old_cache.db_set("replicated", 1)
	old_cache.db_set("accessed_at", "2019-01-01 00:00:00")

	new_cache = get_cache(new_file.name)
	new_cache.db_set("replicated", 1)
	new_cache.db_set("accessed_at", frappe.utils.now_datetime())

	budget_bytes = baseline + new_cache.file_size + 10
	with override_cache_settings(max_cache_size_gb=budget_bytes / 1024**3):
		evict_lru_cache()

	old_cache.reload()
	new_cache.reload()
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
	cache.db_set("replicated", 1)
	cache.db_set("accessed_at", frappe.utils.now_datetime())

	with override_cache_settings(max_cache_size_gb=baseline / 1024**3, cache_retention_minutes=60):
		evict_lru_cache()

	cache.reload()
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
	cache.db_set("accessed_at", "2019-01-01 00:00:00")

	budget = baseline / 1024**3
	with override_cache_settings(max_cache_size_gb=budget, emergency_cache_size_gb=budget):
		evict_lru_cache()

	cache.reload()
	assert cache.evicted == 0
	assert os.path.exists(cache.local_path)


def test_emergency_ceiling_ignores_retention_floor(mocked_s3_client):
	# clear other replicated rows so eviction's "oldest" is unambiguous
	frappe.db.set_value(
		"Local File Cache",
		{"evicted": 0, "replicated": 1},
		{"evicted": 1, "evicted_at": frappe.utils.now_datetime()},
		update_modified=False,
	)

	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		replicated_file = create_attached_upload(b"E" * 200, file_name="emergency_replicated.bin")
		unreplicated_file = create_attached_upload(b"F" * 200, file_name="emergency_unreplicated.bin")

	replicated_cache = get_cache(replicated_file.name)
	replicated_cache.db_set("replicated", 1)
	replicated_cache.db_set("accessed_at", frappe.utils.now_datetime())

	unreplicated_cache = get_cache(unreplicated_file.name)
	unreplicated_cache.db_set("accessed_at", frappe.utils.now_datetime())

	emergency_budget = get_cached_bytes_total() - replicated_cache.file_size + 10
	with override_cache_settings(
		max_cache_size_gb=1, emergency_cache_size_gb=emergency_budget / 1024**3
	):
		evict_lru_cache()

	replicated_cache.reload()
	unreplicated_cache.reload()
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
	assert not frappe.db.exists("Local File Cache", {"s3_key": ["like", "%ceiling_rejected%"]})


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

	cache.reload()
	assert cache.evicted == 0


def test_migration_does_not_populate_cache(mocked_s3_client):
	file = create_local_only_file(b"migration content", file_name="migrate_me.bin")

	before = frappe.db.count("Local File Cache")

	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	), patch("cloud_storage.migration.get_cloud_storage_client", return_value=mocked_s3_client):
		migrate_files(doctype="User")

	after = frappe.db.count("Local File Cache")
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

	assert not frappe.db.exists("Local File Cache", {"file": file.name})
	assert file.s3_key


# Grupo 5 — modo degradado


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
		replicate_cached_file(cache.name)

	cache.reload()
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

	assert not frappe.db.exists("Local File Cache", {"file": file.name})

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

	cache.reload()
	assert frappe.db.exists("Local File Cache", cache.name)
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
	cache.db_set("replicated", 1)
	cache.db_set("accessed_at", "2019-01-01 00:00:00")

	budget_bytes = baseline + 10
	with degraded_cloud_storage(), override_cache_settings(
		max_cache_size_gb=budget_bytes / 1024**3
	):
		evict_lru_cache()

	cache.reload()
	assert cache.evicted == 0
	assert os.path.exists(cache.local_path)


# Grupo 6 — recuperación


def test_recovery_drains_backlog_oldest_first(mocked_s3_client):
	# neutralize stray unreplicated rows from other tests so the sweep below is unambiguous
	frappe.db.set_value(
		"Local File Cache",
		{"replicated": 0, "pending_delete": 0},
		"replicated",
		1,
		update_modified=False,
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
	frappe.db.set_value("Local File Cache", older_cache.name, "creation", "2000-01-01 00:00:00")
	frappe.db.set_value("Local File Cache", newer_cache.name, "creation", "2000-01-01 00:00:01")

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	), patch(
		"cloud_storage.cloud_storage.tasks.replicate_cached_file", wraps=replicate_cached_file
	) as spy:
		retry_pending_replications()

	assert [call.args[0] for call in spy.call_args_list] == [older_cache.name, newer_cache.name]

	older_cache.reload()
	newer_cache.reload()
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
		replicate_cached_file(cache.name)

	assert mocked_s3_client.head_object(Bucket=mocked_s3_client.bucket, Key=s3_key)

	with degraded_cloud_storage(), patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		frappe.delete_doc("File", file.name, ignore_permissions=True)

	cache.reload()
	assert cache.pending_delete == 1

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	):
		process_pending_deletes()

	assert not frappe.db.exists("Local File Cache", cache.name)
	with pytest.raises(ClientError):
		mocked_s3_client.head_object(Bucket=mocked_s3_client.bucket, Key=s3_key)


def test_replication_retries_stop_at_max_retries(mocked_s3_client):
	# neutralize stray unreplicated rows from other tests, same as the backlog-order test above
	frappe.db.set_value(
		"Local File Cache",
		{"replicated": 0, "pending_delete": 0},
		"replicated",
		1,
		update_modified=False,
	)

	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	), patch.object(mocked_s3_client, "put_object", side_effect=down_endpoint_error()):
		below_cap = create_attached_upload(b"below cap retry", file_name="retry_below_cap.bin")
		at_cap = create_attached_upload(b"at cap retry", file_name="retry_at_cap.bin")

	below_cap_cache = get_cache(below_cap.name)
	at_cap_cache = get_cache(at_cap.name)
	below_cap_cache.db_set("replication_attempts", 2)
	at_cap_cache.db_set("replication_attempts", 3)

	with override_cache_settings(replication_max_retries=3), patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	), patch(
		"cloud_storage.cloud_storage.tasks.replicate_cached_file", wraps=replicate_cached_file
	) as spy:
		retry_pending_replications()

	called_names = [call.args[0] for call in spy.call_args_list]
	assert below_cap_cache.name in called_names
	assert at_cap_cache.name not in called_names

	below_cap_cache.reload()
	at_cap_cache.reload()
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
	cache.db_set("replication_attempts", 25)
	cache.db_set("last_replication_error", "stale error from before the outage ended")
	frappe.db.set_single_value(
		"Cloud Storage Health",
		{"status": "Degraded", "consecutive_failures": 5, "degraded_since": frappe.utils.now_datetime()},
	)

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	):
		check_cloud_health()

	assert frappe.get_single("Cloud Storage Health").status == "Healthy"

	cache.reload()
	assert cache.replication_attempts == 0
	assert not cache.last_replication_error

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	):
		retry_pending_replications()

	cache.reload()
	assert cache.replicated == 1
	assert not cache.last_replication_error
	assert mocked_s3_client.head_object(Bucket=mocked_s3_client.bucket, Key=cache.s3_key)


# Grupo 8 — reconciliación al iniciar


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

	cache.reload()
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
	frappe.db.set_value(
		"Local File Cache", cache.name, "local_path", differently_spelled_path, update_modified=False
	)

	reconcile_local_cache()

	cache.reload()
	assert cache.evicted == 0
	assert os.path.exists(real_path)


def test_replication_enqueued_after_commit(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	), patch("frappe.enqueue") as mock_enqueue:
		create_attached_upload(b"enqueue after commit", file_name="enqueue_after_commit.bin")

	assert mock_enqueue.call_args.kwargs["enqueue_after_commit"] is True


def test_local_path_accepts_paths_longer_than_140_chars():
	long_path = "/" + ("a" * 200) + "/file.bin"
	doc = frappe.get_doc(
		{"doctype": "Local File Cache", "file": "local-path-length-test", "local_path": long_path}
	).insert(ignore_permissions=True)
	try:
		doc.reload()
		assert doc.local_path == long_path
	finally:
		frappe.delete_doc("Local File Cache", doc.name, ignore_permissions=True)


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
	assert not frappe.db.exists(
		"Local File Cache", {"s3_key": ["like", "%ceiling_incoming_rejected%"]}
	)


def test_admit_cache_record_cleans_up_bytes_on_insert_failure(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		file = create_uncached_cloud_file(b"admit failure cleanup", file_name="admit_failure.bin")

	local_path = get_local_cache_path(file.content_hash)
	with open(local_path, "wb") as fh:
		fh.write(b"admit failure cleanup")

	with patch("frappe.model.document.Document.insert", side_effect=Exception("boom")):
		with pytest.raises(Exception, match="boom"):
			admit_local_cache_record(file, local_path)

	assert not os.path.exists(local_path)


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
	assert frappe.get_single("Cloud Storage Health").status != "Degraded"

	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	), patch.object(mocked_s3_client, "delete_object", side_effect=down_endpoint_error()):
		frappe.delete_doc("File", file.name, ignore_permissions=True)

	assert not frappe.db.exists("File", file.name)
	assert not os.path.exists(local_path)

	cache.reload()
	assert frappe.db.exists("Local File Cache", cache.name)
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


def test_check_cloud_health_drains_backlog_immediately_on_recovery(mocked_s3_client):
	frappe.db.set_value(
		"Local File Cache",
		{"replicated": 0, "pending_delete": 0},
		"replicated",
		1,
		update_modified=False,
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

	frappe.db.set_single_value(
		"Cloud Storage Health",
		{"status": "Degraded", "consecutive_failures": 5, "degraded_since": frappe.utils.now_datetime()},
	)

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	):
		check_cloud_health()

	assert frappe.get_single("Cloud Storage Health").status == "Healthy"
	cache.reload()
	assert cache.replicated == 1


def test_degraded_status_ignored_when_cache_disabled():
	frappe.db.set_single_value("Cloud Storage Health", "status", "Degraded")
	try:
		with override_cache_settings(local_cache_enabled=False):
			assert is_cloud_storage_degraded() is False
	finally:
		frappe.db.set_single_value("Cloud Storage Health", "status", "Healthy")


def test_retrieve_redirects_when_cache_disabled_even_if_status_stuck_degraded(mocked_s3_client):
	frappe.db.set_single_value("Cloud Storage Health", "status", "Degraded")
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
		frappe.db.set_single_value("Cloud Storage Health", "status", "Healthy")


def test_replication_deletes_object_when_row_and_file_vanish_mid_upload(mocked_s3_client):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		content = b"orphaned by concurrent delete"
		file = create_attached_upload(content, file_name="orphan_race.bin")

	cache_name = get_cache(file.name).name
	s3_key = get_cache(file.name).s3_key
	file_name = file.name

	original_put_object = mocked_s3_client.put_object

	def put_then_vanish(*args, **kwargs):
		response = original_put_object(*args, **kwargs)
		frappe.db.delete("Local File Cache", {"name": cache_name})
		frappe.db.delete("File", {"name": file_name})
		return response

	with patch(
		"cloud_storage.cloud_storage.tasks.get_cloud_storage_client", return_value=mocked_s3_client
	), patch.object(mocked_s3_client, "put_object", side_effect=put_then_vanish):
		replicate_cached_file(cache_name)

	with pytest.raises(ClientError):
		mocked_s3_client.head_object(Bucket=mocked_s3_client.bucket, Key=s3_key)


def test_local_cache_enabled_rejects_use_local():
	with override_cache_settings(use_local=True, local_cache_enabled=True):
		with pytest.raises(frappe.ValidationError):
			validate_config()
