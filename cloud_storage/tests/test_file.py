from unittest.mock import MagicMock, patch

import frappe
from boto3.exceptions import S3UploadFailedError
from frappe.tests.utils import FrappeTestCase

from cloud_storage.cloud_storage.overrides.file import upload_file


class TestFile(FrappeTestCase):
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
