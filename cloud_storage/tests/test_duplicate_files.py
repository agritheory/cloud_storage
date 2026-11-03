# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

from pathlib import Path
from uuid import uuid4

import frappe
import pytest

from cloud_storage.cloud_storage.overrides import file as file_module
from cloud_storage.cloud_storage.overrides.file import (
	FILE_URL,
	get_cloud_storage_client,
	get_same_name_attachment,
	key_in_use,
)
from cloud_storage.tests.test_file import create_upload_file


@pytest.fixture(params=[1, 0], ids=["legacy_paths", "flat_paths"])
def path_mode(request, monkeypatch):
	"""Run each test with both key layouts; flat paths are where same-name keys collide."""
	settings = dict(frappe.conf.cloud_storage_settings)
	settings["use_legacy_paths"] = request.param
	monkeypatch.setitem(frappe.conf, "cloud_storage_settings", settings)
	return request.param


@pytest.fixture
def file_name():
	# unique per test so files from earlier tests in the session never match
	return f"dup_{uuid4().hex[:8]}.txt"


def write(tmp_path: Path, name: str, content: str) -> Path:
	path = tmp_path / uuid4().hex / name
	path.parent.mkdir()
	path.write_text(content)
	return path


def unique_content() -> str:
	return f"content {uuid4().hex}\n"


def read_object(key: str) -> bytes:
	client = get_cloud_storage_client()
	return client.get_object(Bucket=client.bucket, Key=key)["Body"].read()


def files_named_like(file_name: str) -> list[str]:
	stem = Path(file_name).stem
	return frappe.get_all("File", filters={"file_name": ["like", f"{stem}%"]}, pluck="name")


def test_same_name_different_content_on_different_documents(path_mode, file_name, tmp_path):
	content_a, content_b = unique_content(), unique_content()
	first = create_upload_file(write(tmp_path, file_name, content_a), file_name=file_name)
	second = create_upload_file(
		write(tmp_path, file_name, content_b),
		file_name=file_name,
		doctype="Module Def",
		docname="Automation",
	)

	first.load_from_db()
	second.load_from_db()

	# a different document's file of the same name is a separate File, not a new version
	assert first.name != second.name
	assert first.s3_key != second.s3_key
	assert len(first.file_association) == 1
	assert first.file_association[0].link_doctype == "User"
	assert len(second.file_association) == 1
	assert second.file_association[0].link_doctype == "Module Def"

	# neither upload overwrote the other's object
	assert read_object(first.s3_key) == content_a.encode()
	assert read_object(second.s3_key) == content_b.encode()
	assert first.file_url == FILE_URL.format(path=first.s3_key)
	assert second.file_url == FILE_URL.format(path=second.s3_key)


def test_colliding_key_gets_content_hash_suffix(file_name, tmp_path, monkeypatch):
	settings = dict(frappe.conf.cloud_storage_settings)
	settings["use_legacy_paths"] = 0
	monkeypatch.setitem(frappe.conf, "cloud_storage_settings", settings)

	first = create_upload_file(write(tmp_path, file_name, unique_content()), file_name=file_name)
	second = create_upload_file(
		write(tmp_path, file_name, unique_content()),
		file_name=file_name,
		doctype="Module Def",
		docname="Automation",
	)
	second.load_from_db()

	folder = get_cloud_storage_client().folder
	stem, extn = file_name.rsplit(".", 1)
	expected_name = f"{stem}{second.content_hash[-6:]}.{extn}"

	assert first.s3_key == f"{folder}/{file_name}"
	assert second.file_name == expected_name
	assert second.s3_key == f"{folder}/{expected_name}"


def test_same_name_different_content_on_same_document_creates_version(
	path_mode, file_name, tmp_path
):
	first = create_upload_file(write(tmp_path, file_name, unique_content()), file_name=file_name)
	original_key = first.s3_key

	updated_content = unique_content()
	second = create_upload_file(write(tmp_path, file_name, updated_content), file_name=file_name)
	first.load_from_db()

	# the upload is a new version of the existing File, stored under the same key; the
	# record created for the upload is merged into it rather than left pointing at that key
	assert files_named_like(file_name) == [first.name]
	assert second.name == first.name or not frappe.db.exists("File", second.name)
	assert first.s3_key == original_key
	assert first.file_name == file_name
	assert first.content_hash == second.content_hash
	assert [(a.link_doctype, a.link_name) for a in first.file_association] == [
		("User", "Administrator")
	]
	assert read_object(original_key) == updated_content.encode()
	assert len(first.versions) >= 2
	assert first.versions[-1].version != first.versions[0].version


def test_same_content_reuses_existing_file(path_mode, file_name, tmp_path):
	content = unique_content()
	first = create_upload_file(write(tmp_path, file_name, content), file_name=file_name)
	other_name = f"other_{file_name}"
	create_upload_file(
		write(tmp_path, other_name, content),
		file_name=other_name,
		doctype="Module Def",
		docname="Automation",
	)
	first.load_from_db()

	# identical bytes are stored once and associated with both documents, once each
	assert frappe.get_all("File", filters={"content_hash": first.content_hash}, pluck="name") == [
		first.name
	]
	assert sorted((a.link_doctype, a.link_name) for a in first.file_association) == [
		("Module Def", "Automation"),
		("User", "Administrator"),
	]
	assert first.s3_key
	assert read_object(first.s3_key) == content.encode()


def test_same_name_matched_through_file_association(path_mode, file_name, tmp_path):
	content = unique_content()
	first = create_upload_file(write(tmp_path, file_name, content), file_name=file_name)
	# same bytes on another document: merged into the first File, which now points there
	create_upload_file(
		write(tmp_path, file_name, content),
		file_name=file_name,
		doctype="Module Def",
		docname="Automation",
	)
	first.load_from_db()
	assert (first.attached_to_doctype, first.attached_to_name) == ("Module Def", "Automation")

	# the User is linked only through File Association, and still owns the file of this name
	assert get_same_name_attachment(file_name, "User", "Administrator") == first.name

	updated_content = unique_content()
	second = create_upload_file(write(tmp_path, file_name, updated_content), file_name=file_name)
	first.load_from_db()

	assert files_named_like(file_name) == [first.name]
	assert second.name == first.name or not frappe.db.exists("File", second.name)
	assert first.content_hash == second.content_hash
	assert read_object(first.s3_key) == updated_content.encode()


def test_get_same_name_attachment_ignores_other_documents(file_name, tmp_path):
	first = create_upload_file(write(tmp_path, file_name, unique_content()), file_name=file_name)

	assert get_same_name_attachment(file_name, "User", "Administrator") == first.name
	assert get_same_name_attachment(file_name, "User", "Administrator", exclude=first.name) is None
	assert get_same_name_attachment(file_name, "Module Def", "Automation") is None
	assert get_same_name_attachment(file_name, None, None) is None


def test_key_in_use(file_name, tmp_path):
	file = create_upload_file(write(tmp_path, file_name, unique_content()), file_name=file_name)

	assert key_in_use(file.s3_key)
	# a File never conflicts with its own key
	assert not key_in_use(file.s3_key, file.name)
	assert not key_in_use(f"{file.s3_key}.missing")

	# rows written before s3_key was stored are matched on file_url
	frappe.db.set_value("File", file.name, "s3_key", None)
	assert key_in_use(file.s3_key)


def test_failed_upload_raises_and_keeps_existing_object(file_name, tmp_path, monkeypatch):
	content = unique_content()
	first = create_upload_file(write(tmp_path, file_name, content), file_name=file_name)

	client = get_cloud_storage_client()

	def fail(**kwargs):
		raise RuntimeError("upload failed")

	monkeypatch.setattr(client, "put_object", fail)
	monkeypatch.setattr(file_module, "get_cloud_storage_client", lambda: client)
	monkeypatch.setattr(frappe, "log_error", lambda *args, **kwargs: None)

	with pytest.raises(frappe.ValidationError):
		create_upload_file(
			write(tmp_path, file_name, unique_content()),
			file_name=file_name,
			doctype="Module Def",
			docname="Automation",
		)

	monkeypatch.undo()
	assert read_object(first.s3_key) == content.encode()
