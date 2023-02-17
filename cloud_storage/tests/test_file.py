from unittest.mock import MagicMock, patch

import frappe
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import ClientError
from frappe.tests.utils import FrappeTestCase

from cloud_storage.cloud_storage.overrides.file import (
	CustomFile,
	delete_file,
	upload_file,
	write_file,
)


class TestFile(FrappeTestCase):
	@patch("cloud_storage.cloud_storage.overrides.file.upload_file")
	@patch("cloud_storage.cloud_storage.overrides.file.strip_special_chars")
	@patch("frappe.conf")
	def test_write_file(self, config, strip_chars, upload_file):
		file = MagicMock()

		# test local fallback
		config.cloud_storage_settings = None
		write_file(file)
		assert file.save_file_on_filesystem.call_count == 1

		config.cloud_storage_settings = {"use_local": True}
		write_file(file)
		assert file.save_file_on_filesystem.call_count == 2

		config.cloud_storage_settings = {"use_local": False}
		file.attached_to_doctype = "Data Import"
		write_file(file)
		assert file.save_file_on_filesystem.call_count == 3

		# test file upload with autoname
		file.attached_to_doctype = None
		file.name = None
		strip_chars.return_value = "test_file.png"
		upload_file.return_value = file
		write_file(file)
		assert file.autoname.call_count == 1
		upload_file.assert_called_with(file)

		# test file upload without autoname
		file.name = "test_file"
		file.file_name = "test_file.png"
		upload_file.return_value = file
		write_file(file)
		assert file.autoname.call_count == 1
		upload_file.assert_called_with(file)

	@patch("cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client")
	@patch("cloud_storage.cloud_storage.overrides.file.get_file_path")
	def test_upload_file(self, file_path, client):
		# setup file
		file = MagicMock()
		file.content_type = "image/jpeg"

		# test general exception
		client.return_value.put_object.side_effect = TypeError
		upload_file(file)
		assert client.return_value.put_object.call_count == 1

		# test upload errors
		client.return_value.put_object.side_effect = S3UploadFailedError
		with self.assertRaises(frappe.ValidationError):
			upload_file(file)
		assert client.return_value.put_object.call_count == 2

		# test upload success
		client.return_value.put_object.side_effect = True
		file_path.return_value = "/path/to/s3/bucket/location"
		upload_file(file)
		assert client.return_value.put_object.call_count == 3
		self.assertEqual(
			file.file_url,
			"/api/method/retrieve?key=/path/to/s3/bucket/location",
		)

	@patch("cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client")
	@patch("frappe.conf")
	def test_delete_file(self, config, client):
		file = MagicMock()

		# test local fallback
		config.cloud_storage_settings = None
		delete_file(file)
		assert file.delete_file_from_filesystem.call_count == 1
		assert client.return_value.delete_object.call_count == 0

		config.cloud_storage_settings = {"use_local": True}
		delete_file(file)
		assert file.delete_file_from_filesystem.call_count == 2
		assert client.return_value.delete_object.call_count == 0

		# test skip folder deletion
		config.cloud_storage_settings = {"use_local": False}
		file.is_folder = True
		delete_file(file)
		assert client.return_value.delete_object.call_count == 0

		# test skip file deletion from missing url or key param
		file.is_folder = False
		file.file_url = None
		delete_file(file)
		assert client.return_value.delete_object.call_count == 0

		file.file_url = "/api/method/retrieve"
		delete_file(file)
		assert client.return_value.delete_object.call_count == 0

		file.file_url = "/api/method/retrieve?key="
		delete_file(file)
		assert client.return_value.delete_object.call_count == 0

		# test general exception
		file.file_url = "/api/method/retrieve?key=/path/to/s3/bucket/location"
		client.return_value.delete_object.side_effect = TypeError
		delete_file(file)
		assert client.return_value.delete_object.call_count == 1

		# test upload errors
		client.return_value.delete_object.side_effect = ClientError(
			error_response={"Error": {}}, operation_name="delete_file"
		)
		with self.assertRaises(frappe.ValidationError):
			delete_file(file)
		assert client.return_value.delete_object.call_count == 2

		# test upload success
		client.bucket = "bucket"
		client.return_value.delete_object.side_effect = True
		delete_file(file)
		assert client.return_value.delete_object.call_count == 3

	@patch("frappe.db.exists")
	@patch("frappe.has_permission")
	@patch("frappe.get_doc")
	def test_file_permission(self, get_doc, has_permission, db_exists):
		# test file access for owner
		file = CustomFile({"doctype": "File", "owner": "Administrator"})
		self.assertEqual(file.has_permission(), True)
		self.assertEqual(file.has_permission(user="Administrator"), True)

		# test file access for non-owner user
		has_permission.return_value = True
		assert file.has_permission(user="Administrator") is True
		assert file.has_permission(user="support@agritheory.dev") is True
		has_permission.return_value = False
		assert file.has_permission(user="Administrator") is True
		assert file.has_permission(user="support@agritheory.dev") is False

		# test file access for attached doctypes
		file = CustomFile(
			{
				"doctype": "File",
				"owner": "Administrator",
				"attached_to_doctype": "Sales Order",
				"attached_to_name": "SO-0001",
			}
		)

		get_doc.return_value = MagicMock()
		get_doc.return_value.has_permission.return_value = True
		assert file.has_permission(user="Administrator") is True
		assert file.has_permission(user="support@agritheory.dev") is True
		get_doc.return_value.has_permission.return_value = False
		assert file.has_permission(user="Administrator") is True
		assert file.has_permission(user="support@agritheory.dev") is False

		# test file access for custom user permissions
		get_doc.return_value = {}
		db_exists.return_value = True
		assert file.has_permission(user="Administrator") is True
		assert file.has_permission(user="support@agritheory.dev") is True
		db_exists.return_value = False
		assert file.has_permission(user="Administrator") is True
		assert file.has_permission(user="support@agritheory.dev") is False
