from unittest.mock import MagicMock, patch

import frappe
from boto3.exceptions import S3UploadFailedError
from frappe.tests.utils import FrappeTestCase

from cloud_storage.cloud_storage.overrides.file import upload_file, write_file


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
		config.cloud_storage_settings = {"use_local": False}
		file.attached_to_doctype = None
		file.name = None
		strip_chars.return_value = "test_file.png"
		upload_file.return_value = file
		write_file(file)
		assert file.save_file_on_filesystem.call_count == 3
		assert file.autoname.call_count == 1
		upload_file.assert_called_with(file)

		# test file upload without autoname
		config.cloud_storage_settings = {"use_local": False}
		file.name = "test_file"
		file.file_name = "test_file.png"
		upload_file.return_value = file
		write_file(file)
		assert file.save_file_on_filesystem.call_count == 3
		assert file.autoname.call_count == 1
		upload_file.assert_called_with(file)

	@patch("cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client")
	@patch("cloud_storage.cloud_storage.overrides.file.get_file_path")
	def test_upload_file(self, file_path, client):
		# setup file
		file_path.return_value = "/path/to/s3/bucket/location"
		file = MagicMock()
		file.content_type = "image/jpeg"

		# test general exception
		client.return_value.put_object.side_effect = TypeError
		upload_file(file)

		# test upload errors
		client.return_value.put_object.side_effect = S3UploadFailedError
		with self.assertRaises(frappe.ValidationError):
			upload_file(file)

		# test upload success
		client.return_value.put_object.side_effect = True
		upload_file(file)
		self.assertEqual(
			file.file_url,
			"/api/method/cloud_storage.cloud_storage.overrides.file.retrieve?key=/path/to/s3/bucket/location",
		)
