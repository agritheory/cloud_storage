# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import frappe
import pytest
from conftest import mocked_s3_client
from moto import mock_s3
from werkzeug.datastructures import FileMultiDict

from cloud_storage.cloud_storage.overrides.file import CloudStorageFile, retrieve
from cloud_storage.migration import migrate_files


@pytest.fixture
def example_file_record_0():
	return Path(__file__).parent / "fixtures" / "aticonrusthex.png"


@pytest.fixture
def example_file_record_1():
	return Path(__file__).parent / "fixtures" / "aticonrust.svg"


@pytest.fixture
def example_file_record_2():
	return Path(__file__).parent / "fixtures" / "atlogo_rust.svg"


@pytest.fixture
def example_file_record_4():
	return Path(__file__).parent / "fixtures" / "sample.doc"


@pytest.fixture
def example_file_record_5():
	return Path(__file__).parent / "fixtures" / "sample.csv"


@pytest.fixture
def example_file_record_6():
	return Path(__file__).parent / "fixtures" / "diamo.png"


@pytest.fixture
def get_cloud_storage_client_fixture():
	return frappe.call("cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client")


# helper function
def create_upload_file(file_path: Path, **kwargs) -> CloudStorageFile:
	f = BytesIO(file_path.resolve().read_bytes())
	f.seek(0)

	# simulate a Frappe client -> server request file object using Werkzeug
	files = FileMultiDict()
	files.add_file("file", f, kwargs.get("file_name"))

	frappe.set_user("Administrator")
	frappe.local.request = frappe._dict()
	frappe.local.request.method = kwargs.get("method") or "POST"
	frappe.local.request.files = files
	frappe.local.form_dict = frappe._dict()
	frappe.local.form_dict.is_private = False
	frappe.local.form_dict.doctype = kwargs.get("doctype") or "User"
	frappe.local.form_dict.docname = kwargs.get("docname") or "Administrator"
	frappe.local.form_dict.fieldname = kwargs.get("fieldname") or None
	frappe.local.form_dict.file_url = kwargs.get("file_url") or None
	frappe.local.form_dict.folder = kwargs.get("folder") or "Home"
	frappe.local.form_dict.file_name = kwargs.get("file_name") or None
	frappe.local.form_dict.optimize = kwargs.get("optimize") or False
	file = frappe.call("frappe.handler.upload_file")
	return file


def save_file_locally_if_no_cloud_storage(file):
	"""
	Save the file on the local filesystem if cloud storage is not configured or use_local is True.
	Returns the file object after saving.
	"""
	if not getattr(frappe.conf, "cloud_storage_settings", None) or (
		frappe.conf.cloud_storage_settings and frappe.conf.cloud_storage_settings.get("use_local", False)
	):
		file.save_file_on_filesystem()
		return file
	return file


@mock_s3
def test_config(get_cloud_storage_client_fixture):
	c = get_cloud_storage_client_fixture
	assert c.bucket == "test_bucket"
	assert c.folder == "test_folder"
	assert c.expiration == 110
	assert c._endpoint._endpoint_prefix == "s3"
	assert c._endpoint.host == "https://test.imgainarys3.edu"


@mock_s3
def test_upload_file(example_file_record_0):
	frappe.set_user("Administrator")
	file = create_upload_file(example_file_record_0, file_name="aticonrusthex.png")
	assert frappe.db.exists("File", file.name)
	assert file.attached_to_doctype == "User"
	assert file.attached_to_name == "Administrator"
	assert file.attached_to_field is None
	assert file.folder == "Home"
	assert file.file_name == "aticonrusthex.png"
	assert file.content_hash is not None
	assert (
		file.file_url == "/api/method/retrieve?key=test_folder/User/Administrator/aticonrusthex.png"
	)
	assert file.is_private == 0  # makes the delete file test easier
	assert file.s3_key is not None
	assert len(file.file_association) == 1
	assert file.file_association[0].link_doctype == "User"
	assert file.file_association[0].link_name == "Administrator"

	# Test manual association
	file.append("file_association", {"link_doctype": "Module Def", "link_name": "Cloud Storage"})
	file.save()
	assert len(file.file_association) == 2
	assert file.file_association[1].link_doctype == "Module Def"
	assert file.file_association[1].link_name == "Cloud Storage"


@mock_s3
def test_upload_file_with_multiple_association(example_file_record_1):
	_file = create_upload_file(example_file_record_1, file_name="aticonrust.svg")
	file = create_upload_file(
		example_file_record_1,
		doctype="Module Def",
		docname="Automation",
		file_name="aticonrust.svg",
	)

	_file.load_from_db()
	assert frappe.db.exists("File", _file.name) is not None
	assert frappe.db.exists("File", file.name) is None
	assert len(_file.file_association) >= 2
	assert _file.file_association[0].link_doctype == "User"
	assert _file.file_association[0].link_name == "Administrator"
	assert _file.file_association[1].link_doctype == "Module Def"
	assert _file.file_association[1].link_name == "Automation"


@mock_s3
def test_delete_file(example_file_record_2):
	frappe.set_user("Administrator")
	file = create_upload_file(example_file_record_2, file_name="atlogo_rust.svg")
	s3_key = file.s3_key
	file.delete()

	assert not frappe.db.exists("File", file.name)

	with pytest.raises(frappe.exceptions.DoesNotExistError):
		retrieve(s3_key)


@mock_s3
def test_save_file_without_S3_and_preview(example_file_record_4, example_file_record_6):
	"""
	Test that save_file_locally_if_no_cloud_storage saves the file locally and preview features work.
	"""
	frappe.set_user("Administrator")
	# Unset cloud storage settings
	if hasattr(frappe.conf, "cloud_storage_settings"):
		old_settings = frappe.conf.cloud_storage_settings
		frappe.conf.cloud_storage_settings = None
	else:
		old_settings = None

	try:
		file = create_upload_file(example_file_record_4, file_name="sample.doc")
		file = save_file_locally_if_no_cloud_storage(file)
		assert frappe.db.exists("File", file.name)
		assert file.file_name == "sample.doc"
		content = file.get_content()
		assert content is not None
		path = file.get_full_path()
		assert Path(path).exists()
	finally:
		# Restore settings
		if old_settings is not None:
			frappe.conf.cloud_storage_settings = old_settings


def test_file_versioning_with_content_change(mocked_s3_client, example_file_record_5, tmp_path):
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		frappe.set_user("Administrator")
		file1 = create_upload_file(example_file_record_5, file_name="sample.csv")
		assert frappe.db.exists("File", file1.name)
		original_content_hash = file1.content_hash

		modified_csv = tmp_path / "sample.csv"
		with open(example_file_record_5) as src, open(modified_csv, "w") as dst:
			lines = src.readlines()
			dst.writelines(lines)
			dst.write("4,5,6\n")

		file2 = create_upload_file(modified_csv, file_name="sample.csv")
		file1.load_from_db()

		assert not frappe.db.exists("File", file2.name)
		assert file1.content_hash == file2.content_hash
		assert file1.content_hash != original_content_hash
		assert len(file1.versions) >= 2
		# Optionally, check that the latest version is the most recent
		latest_version = file1.versions[-1]
		assert latest_version.user == "Administrator"
		assert latest_version.version is not None


def test_file_versioning_different_documents(mocked_s3_client, example_file_record_5, tmp_path):
	"""Same-named file uploaded to two different documents should produce one File record."""
	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	):
		frappe.set_user("Administrator")
		file1 = create_upload_file(
			example_file_record_5, file_name="sample_multi.csv", doctype="User", docname="Administrator"
		)
		assert frappe.db.exists("File", file1.name)

		file2 = create_upload_file(
			example_file_record_5, file_name="sample_multi.csv", doctype="User", docname="Guest"
		)
		file1.load_from_db()

		# No duplicate record should exist
		assert not frappe.db.exists("File", file2.name)
		# Existing record must keep its s3_key
		assert file1.s3_key is not None
		# Both documents should be associated
		link_doctypes_names = [(fa.link_doctype, fa.link_name) for fa in file1.file_association]
		assert ("User", "Administrator") in link_doctypes_names
		assert ("User", "Guest") in link_doctypes_names


def test_migration_command(mocked_s3_client, example_file_record_6):
	"""
	Test that save_file_locally_if_no_cloud_storage saves the file locally and preview features work.
	"""
	frappe.set_user("Administrator")
	# Unset cloud storage settings
	if hasattr(frappe.conf, "cloud_storage_settings"):
		old_settings = frappe.conf.cloud_storage_settings
		frappe.conf.cloud_storage_settings = None
	else:
		old_settings = None

	try:
		file = create_upload_file(example_file_record_6, file_name="diamo.png")
		file = save_file_locally_if_no_cloud_storage(file)
		assert frappe.db.exists("File", file.name)
		assert file.file_name == "diamo.png"
		assert file.s3_key is None
		content = file.get_content()
		assert content is not None
		path = file.get_full_path()
		assert Path(path).exists()
		original_file_size = Path(path).stat().st_size
	finally:
		# Restore settings
		if old_settings is not None:
			frappe.conf.cloud_storage_settings = old_settings

	with patch(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		return_value=mocked_s3_client,
	), patch("cloud_storage.migration.get_cloud_storage_client", return_value=mocked_s3_client):
		migrate_files(doctype="User")

	file = frappe.get_doc("File", file.name)

	assert file.s3_key is not None
	assert file.file_url is not None
	assert len(file.file_association) == 1
	assert file.file_association[0].link_doctype == "User"
	assert file.file_association[0].link_name == "Administrator"

	response = mocked_s3_client.head_object(Bucket=mocked_s3_client.bucket, Key=file.s3_key)
	s3_file_size = response["ContentLength"]

	print(f"Original file size: {original_file_size} bytes")
	print(f"S3 file size: {s3_file_size} bytes")

	assert (
		s3_file_size == original_file_size
	), f"File size mismatch: local={original_file_size}, s3={s3_file_size}"
