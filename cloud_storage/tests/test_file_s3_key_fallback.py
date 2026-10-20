# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

from unittest.mock import MagicMock, patch

import frappe
import pytest

from cloud_storage.cloud_storage.overrides.file import CloudStorageFile

KEY = "cmis-files/bc15d3b311/Sanction_Orders(Ed.Tour).pdf"


def _file(s3_key, file_url):
	doc = CloudStorageFile.__new__(CloudStorageFile)
	doc.name = "dup"
	doc.s3_key = s3_key
	doc.file_url = file_url
	return doc


def test_get_s3_key_prefers_stored_key():
	assert _file("stored", f"/api/method/retrieve?key={KEY}").get_s3_key() == "stored"


def test_get_s3_key_falls_back_to_url():
	assert _file(None, f"/api/method/retrieve?key={KEY}").get_s3_key() == KEY


def test_get_s3_key_without_any_key_throws():
	with pytest.raises(frappe.ValidationError):
		_file(None, "/files/x.pdf").get_s3_key()


def test_get_content_uses_url_key_for_keyless_duplicate():
	client = MagicMock(bucket="bucket")
	client.get_object.return_value = {"Body": MagicMock(read=lambda: b"pdf")}
	doc = _file(None, f"/api/method/retrieve?key={KEY}")
	doc.is_folder = 0
	doc.content = None
	with patch("cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client", return_value=client), patch.object(
		CloudStorageFile, "validate_file_url"
	):
		assert doc.get_content() == b"pdf"
	client.get_object.assert_called_once_with(Bucket="bucket", Key=KEY)
