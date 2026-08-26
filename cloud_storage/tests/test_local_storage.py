# Copyright (c) 2026
# Tests for CloudStorageFile when cloud storage is disabled

from io import BytesIO
from pathlib import Path

import frappe
import pytest
from werkzeug.datastructures import FileMultiDict

from cloud_storage.cloud_storage.overrides.file import CloudStorageFile


@pytest.fixture(autouse=True)
def disable_cloud_storage():
	"""Force local file storage for all tests"""
	old = getattr(frappe.conf, "cloud_storage_settings", None)

	frappe.conf.cloud_storage_settings = {"use_local": True}

	yield

	frappe.conf.cloud_storage_settings = old


@pytest.fixture
def example_file(tmp_path):
	f = tmp_path / "sample.txt"
	f.write_text("hello world")
	return f


def create_upload_file(file_path: Path, **kwargs) -> CloudStorageFile:
	f = BytesIO(file_path.read_bytes())

	files = FileMultiDict()
	files.add_file("file", f, kwargs.get("file_name") or file_path.name)

	frappe.set_user("Administrator")

	frappe.local.request = frappe._dict()
	frappe.local.request.method = "POST"
	frappe.local.request.files = files

	frappe.local.form_dict = frappe._dict(
		doctype="User",
		docname="Administrator",
		is_private=0,
		folder="Home",
		file_name=file_path.name,
	)

	return frappe.call("frappe.handler.upload_file")


def test_upload_local_file(example_file):
	file = create_upload_file(example_file)

	assert frappe.db.exists("File", file.name)

	assert file.file_name.startswith("sample")
	assert file.file_name.endswith(".txt")

	assert file.s3_key is None


def test_get_file_content(example_file):
	file = create_upload_file(example_file)

	content = file.get_content()

	assert content is not None
	assert "hello world" in str(content)


def test_get_full_path(example_file):
	file = create_upload_file(example_file)

	path = file.get_full_path()

	assert Path(path).exists()


def test_file_association(example_file):
	file = create_upload_file(example_file)

	assert file.attached_to_doctype == "User"
	assert file.attached_to_name == "Administrator"


def test_file_versioning_local(example_file, tmp_path):
	file1 = create_upload_file(example_file)

	modified = tmp_path / "sample.txt"
	modified.write_text("hello world modified")

	create_upload_file(modified)

	# get latest file record
	file_name = frappe.db.get_value(
		"File",
		{"file_name": ["like", "sample%"]},
		"name",
		order_by="creation desc",
	)

	file = frappe.get_doc("File", file_name)

	assert file is not None
	assert file.content_hash is not None


def test_delete_file_local(example_file):
	file = create_upload_file(example_file)

	path = file.get_full_path()

	assert Path(path).exists()

	file.delete()

	assert not frappe.db.exists("File", file.name)


def test_get_content_rejects_absolute_path_escape():
	"""An absolute file_name escapes the files/ sandbox via os.path.join."""
	frappe.set_user("Administrator")
	file = frappe.new_doc("File")
	file.file_name = "/etc/hostname"
	file.file_url = ""
	file.is_private = 0

	with pytest.raises(frappe.ValidationError):
		file.get_content()


def test_get_content_rejects_relative_path_escape():
	"""Same escape, via ../ traversal instead of an absolute path."""
	frappe.set_user("Administrator")
	file = frappe.new_doc("File")
	file.file_name = "../" * 10 + "etc/hostname"
	file.file_url = ""
	file.is_private = 1

	with pytest.raises(frappe.ValidationError):
		file.get_content()


def test_get_content_rejects_sibling_directory_escape():
	"""A sibling dir that merely shares the files/ prefix as a string (files-shadow)
	must not pass containment just because startswith(base_path) is true."""
	frappe.set_user("Administrator")
	shadow_dir = Path(frappe.get_site_path("private")) / "files-shadow"
	shadow_dir.mkdir(exist_ok=True)
	secret = shadow_dir / "secret.txt"
	secret.write_text("outside the sandbox")

	file = frappe.new_doc("File")
	file.file_name = "../files-shadow/secret.txt"
	file.file_url = ""
	file.is_private = 1

	try:
		with pytest.raises(frappe.ValidationError):
			file.get_content()
	finally:
		secret.unlink()
		shadow_dir.rmdir()
