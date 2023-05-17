from io import BytesIO
from pathlib import Path

import pytest
from moto import mock_s3

import frappe


@pytest.fixture
def example_file_record_0():
	return Path(__file__).parent / "fixtures" / "aticonrusthex.png"


@pytest.fixture
def get_cloud_storage_client_fixture():
	return frappe.call("cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client")


@mock_s3
def test_config(get_cloud_storage_client_fixture):
	c = get_cloud_storage_client_fixture
	assert c.bucket == "test_bucket"
	assert c.folder == "test_folder"
	assert c.expiration == 110
	assert c._endpoint._endpoint_prefix == "s3"
	assert c._endpoint.host == "https://test.imgainarys3.edu"


# helper function
def create_upload_file(file_path, **kwargs):
	f = BytesIO(file_path.resolve().read_bytes())
	f.seek(0)
	frappe.set_user("Administrator")
	frappe.local.request = frappe._dict()
	frappe.local.request.method = kwargs.get("method") or "POST"
	frappe.local.request.files = f
	frappe.local.form_dict = frappe._dict()
	frappe.local.form_dict.is_private = True
	frappe.local.form_dict.doctype = kwargs.get("doctype") or "User"
	frappe.local.form_dict.docname = kwargs.get("docname") or "Administrator"
	frappe.local.form_dict.fieldname = kwargs.get("fieldname") or "image"
	frappe.local.form_dict.file_url = kwargs.get("file_url") or None
	frappe.local.form_dict.folder = kwargs.get("folder") or "Home"
	frappe.local.form_dict.file_name = kwargs.get("file_name") or "aticonrusthex.png"
	frappe.local.form_dict.optimize = kwargs.get("optimize") or False
	return frappe.call("frappe.handler.upload_file")


@mock_s3
def test_upload_file(example_file_record_0):
	file = create_upload_file(example_file_record_0)
	assert frappe.db.exists("File", file.name)
	assert file.attached_to_doctype == "User"
	assert file.attached_to_name == "Administrator"
	assert file.attached_to_field == "image"
	assert file.folder == "Home"
	assert file.file_name == "aticonrusthex.png"
	assert file.content_hash is None
	assert (
		file.file_url == "/api/method/retrieve?key=test_folder/User/Administrator/aticonrusthex.png"
	)
	assert file.is_private == 1
	assert len(file.file_association) == 1
	assert file.file_association[0].link_doctype == "User"
	assert file.file_association[0].link_name == "Administrator"
	file.append("file_association", {"link_doctype": "Module Def", "link_name": "Cloud Storage"})
	file.save()
	assert len(file.file_association) == 2
	assert file.file_association[1].link_doctype == "Module Def"
	assert file.file_association[1].link_name == "Cloud Storage"


# @mock_s3
# def test_upload_file_with_association(example_file_record_0):
# 	file = create_upload_file(example_file_record_0)
# 	second_association = create_upload_file(example_file_record_0, doctype='Module Def', docname="Cloud Storage")
# 	print(second_association.__dict__)
# 	assert frappe.db.exists('File', file.name)
# 	assert file.attached_to_doctype == 'User'
# 	assert file.attached_to_name == 'Administrator'
# 	assert file.attached_to_field == 'image'
# 	assert file.folder == 'Home'
# 	assert file.file_name == 'aticonrusthex.png'
# 	assert file.file_url == '/api/method/retrieve?key=test_folder/User/Administrator/aticonrusthex.png'
# 	assert file.is_private == 1
# 	assert len(file.file_association) == 1
# 	assert file.file_association[0].link_doctype == 'User'
# 	assert file.file_association[0].link_name == 'Administrator'
