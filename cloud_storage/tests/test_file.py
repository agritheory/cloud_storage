from io import BytesIO
from pathlib import Path

import frappe
import pytest
from moto import mock_s3
from werkzeug.datastructures import FileMultiDict

from cloud_storage.cloud_storage.overrides.file import CustomFile, retrieve


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
def get_cloud_storage_client_fixture():
	return frappe.call("cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client")


# helper function
def create_upload_file(file_path: Path, **kwargs) -> CustomFile:
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
	assert len(_file.file_association) == 2
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
