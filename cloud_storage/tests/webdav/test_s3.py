# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest
from botocore.exceptions import ClientError

from cloud_storage.tests.fixtures import SHARED_VIEWER
from cloud_storage.tests.webdav.helpers import dav_move, file_name_at, put_file


def custom_path_generator(file, folder):
	return f"custom/{file.name}/{file.file_name}"


def test_put_creates_s3_object(dav_request, s3_backend, track_files):
	frappe.set_user(SHARED_VIEWER)

	name = put_file(dav_request, track_files, "webdav_s3_create.txt", b"s3 content")
	doc = frappe.get_doc("File", name)
	expected_key = f"{s3_backend.folder}/webdav/{doc.name}/webdav_s3_create.txt"
	assert doc.s3_key == expected_key
	assert doc.file_url == f"/api/method/retrieve?key={expected_key}"

	obj = s3_backend.get_object(Bucket=s3_backend.bucket, Key=expected_key)
	assert obj["Body"].read() == b"s3 content"


def test_put_uses_custom_path_generator_hook(dav_request, s3_backend, track_files, monkeypatch):
	frappe.set_user(SHARED_VIEWER)
	real_get_hooks = frappe.get_hooks

	def fake_get_hooks(hook_name=None, *args, **kwargs):
		if hook_name == "cloud_storage_webdav_path_generator":
			return ["cloud_storage.tests.webdav.test_s3.custom_path_generator"]
		return real_get_hooks(hook_name, *args, **kwargs)

	monkeypatch.setattr(frappe, "get_hooks", fake_get_hooks)

	name = put_file(dav_request, track_files, "webdav_s3_hook.txt", b"hook override")
	doc = frappe.get_doc("File", name)
	assert doc.s3_key == f"custom/{doc.name}/webdav_s3_hook.txt"

	obj = s3_backend.get_object(Bucket=s3_backend.bucket, Key=doc.s3_key)
	assert obj["Body"].read() == b"hook override"


def test_put_overwrite_s3_object(dav_request, s3_backend, track_files):
	frappe.set_user(SHARED_VIEWER)

	name = put_file(dav_request, track_files, "webdav_s3_overwrite.txt", b"first")
	doc1 = frappe.get_doc("File", name)
	key = doc1.s3_key
	hash1 = doc1.content_hash

	put2 = dav_request("PUT", "/dav/webdav_s3_overwrite.txt", data=b"second, longer content")
	assert put2.status_code in (200, 201, 204)

	same_name = file_name_at("webdav_s3_overwrite.txt")
	assert same_name == name
	assert frappe.db.count("File", {"file_name": "webdav_s3_overwrite.txt", "folder": "Home"}) == 1

	doc2 = frappe.get_doc("File", name)
	assert doc2.s3_key == key
	assert doc2.content_hash != hash1

	obj = s3_backend.get_object(Bucket=s3_backend.bucket, Key=key)
	assert obj["Body"].read() == b"second, longer content"


def test_move_renames_s3_key_and_removes_old_object(dav_request, s3_backend, track_files):
	frappe.set_user(SHARED_VIEWER)

	name = put_file(dav_request, track_files, "webdav_s3_rename_src.txt", b"rename me")
	old_key = frappe.get_doc("File", name).s3_key

	move_resp = dav_move(dav_request, "/dav/webdav_s3_rename_src.txt", "webdav_s3_rename_dest.txt")
	assert move_resp.status_code in (201, 204)

	same_name = file_name_at("webdav_s3_rename_dest.txt")
	assert same_name == name
	new_doc = frappe.get_doc("File", name)
	assert new_doc.s3_key != old_key

	new_obj = s3_backend.get_object(Bucket=s3_backend.bucket, Key=new_doc.s3_key)
	assert new_obj["Body"].read() == b"rename me"

	with pytest.raises(ClientError):
		s3_backend.head_object(Bucket=s3_backend.bucket, Key=old_key)


def test_move_onto_existing_destination_s3_preserves_identity_and_cleans_up(
	dav_request, s3_backend, track_files
):
	frappe.set_user(SHARED_VIEWER)

	src_name = put_file(dav_request, track_files, "webdav_s3_move_src.txt", b"source content")
	src_key = frappe.get_doc("File", src_name).s3_key

	dest_name = put_file(dav_request, track_files, "webdav_s3_move_dest.txt", b"dest content")
	dest_key = frappe.get_doc("File", dest_name).s3_key

	move_resp = dav_move(dav_request, "/dav/webdav_s3_move_src.txt", "webdav_s3_move_dest.txt")
	assert move_resp.status_code in (201, 204)

	assert not frappe.db.exists("File", src_name)

	dest_doc = frappe.get_doc("File", dest_name)
	assert dest_doc.s3_key == dest_key

	obj = s3_backend.get_object(Bucket=s3_backend.bucket, Key=dest_key)
	assert obj["Body"].read() == b"source content"

	with pytest.raises(ClientError):
		s3_backend.head_object(Bucket=s3_backend.bucket, Key=src_key)

	listing = s3_backend.list_objects_v2(Bucket=s3_backend.bucket)
	keys = [o["Key"] for o in listing.get("Contents", [])]
	assert not any("webdav-overwrite-backup-" in k for k in keys)


def test_move_onto_existing_destination_s3_rolls_back_on_copy_failure(
	dav_request, s3_backend, track_files
):
	frappe.set_user(SHARED_VIEWER)

	src_name = put_file(dav_request, track_files, "webdav_s3_rollback_src.txt", b"source content")
	src_key = frappe.get_doc("File", src_name).s3_key

	dest_name = put_file(
		dav_request, track_files, "webdav_s3_rollback_dest.txt", b"original dest content"
	)
	dest_key = frappe.get_doc("File", dest_name).s3_key

	real_copy_object = s3_backend._client.copy_object
	calls = []
	state = {"real_copy_seen": 0}

	def flaky_copy_object(**kwargs):
		calls.append(kwargs)
		key = kwargs.get("Key", "")
		copy_source_key = kwargs.get("CopySource", {}).get("Key", "")
		is_backup_write = "webdav-overwrite-backup-" in key
		is_restore = "webdav-overwrite-backup-" in copy_source_key
		if not is_backup_write and not is_restore:
			state["real_copy_seen"] += 1
			if state["real_copy_seen"] == 1:
				raise ClientError(
					{"Error": {"Code": "InternalError", "Message": "simulated failure"}},
					"CopyObject",
				)
		return real_copy_object(**kwargs)

	s3_backend.copy_object = flaky_copy_object

	move_resp = dav_move(
		dav_request, "/dav/webdav_s3_rollback_src.txt", "webdav_s3_rollback_dest.txt"
	)
	assert move_resp.status_code == 500

	# the actual backup -> original restore copies ran, not just "nothing
	# was ever touched" (the primary copy fails before writing dest_key or
	# src_key, so content assertions alone can't prove restore_s3_backup ran)
	restore_dest_calls = [
		c
		for c in calls
		if c.get("Key") == dest_key and "webdav-overwrite-backup-" in c["CopySource"]["Key"]
	]
	restore_src_calls = [
		c
		for c in calls
		if c.get("Key") == src_key and "webdav-overwrite-backup-" in c["CopySource"]["Key"]
	]
	assert len(restore_dest_calls) == 1
	assert len(restore_src_calls) == 1

	obj = s3_backend.get_object(Bucket=s3_backend.bucket, Key=dest_key)
	assert obj["Body"].read() == b"original dest content"

	assert frappe.db.exists("File", src_name)
	src_obj = s3_backend.get_object(Bucket=s3_backend.bucket, Key=src_key)
	assert src_obj["Body"].read() == b"source content"

	listing = s3_backend.list_objects_v2(Bucket=s3_backend.bucket)
	keys = [o["Key"] for o in listing.get("Contents", [])]
	assert not any("webdav-overwrite-backup-" in k for k in keys)
