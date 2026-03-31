# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import base64
from pathlib import Path
from unittest.mock import patch, MagicMock

import frappe
import pytest

from cloud_storage.cloud_storage.overrides.file import CloudStorageFile


TEST_FILES = Path(__file__).parent / "fixtures"


def _create_file_doc(file_name, file_url=None, is_private=0):
	doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"file_url": file_url or f"/files/{file_name}",
			"is_private": is_private,
		}
	)

	doc.__class__ = CloudStorageFile
	return doc


@pytest.mark.parametrize("ext", ["doc", "docx"])
def test_doc_preview_content(monkeypatch, ext):
	"""Test preview for doc/docx files using get_content"""

	fake_content = b"fake document content"

	doc = _create_file_doc(f"sample.{ext}")

	monkeypatch.setattr(
		"builtins.open",
		lambda *args, **kwargs: MagicMock(read=lambda: fake_content),
	)

	content = doc.get_content()

	assert content is not None


@pytest.mark.parametrize("ext", ["ppt", "pptx", "odp", "key"])
@patch("cloud_storage.cloud_storage.overrides.file.subprocess.run")
def test_local_presentation_pdf_preview(mock_run, tmp_path, monkeypatch, ext):
	"""Test local presentation preview converted to pdf"""

	pres_file = tmp_path / f"slides.{ext}"
	pres_file.write_bytes(b"fake presentation content")

	pdf_file = tmp_path / "slides.pdf"
	pdf_file.write_bytes(b"%PDF fake")

	doc = _create_file_doc(f"slides.{ext}")
	doc.is_private = 0

	monkeypatch.setattr(
		"frappe.get_site_path",
		lambda *args: str(pres_file),
	)

	mock_run.return_value = None

	with patch(
		"cloud_storage.cloud_storage.overrides.file.Path.with_suffix",
		return_value=pdf_file,
	):
		with patch("builtins.open", lambda *args, **kwargs: pdf_file.open("rb")):
			result = doc.get_pdf_preview()

	decoded = base64.b64decode(result)

	assert decoded.startswith(b"%PDF")


@pytest.mark.parametrize("ext", ["ppt", "pptx", "odp", "key"])
@patch("cloud_storage.cloud_storage.overrides.file.subprocess.run")
def test_s3_presentation_preview(mock_run, mocked_s3_client, monkeypatch, tmp_path, ext):
	"""Test preview when file comes from S3"""

	pres_content = b"presentation binary"

	mocked_s3_client.put_object(
		Bucket=mocked_s3_client.bucket,
		Key=f"slides.{ext}",
		Body=pres_content,
	)

	pdf_file = tmp_path / "slides.pdf"
	pdf_file.write_bytes(b"%PDF mock")

	doc = _create_file_doc(
		f"slides.{ext}",
		file_url=f"/api/method/retrieve?key=slides.{ext}",
	)

	doc.s3_key = f"slides.{ext}"

	monkeypatch.setattr(
		"cloud_storage.cloud_storage.overrides.file.get_cloud_storage_client",
		lambda: mocked_s3_client,
	)

	mock_run.return_value = None

	with patch(
		"builtins.open",
		lambda *args, **kwargs: pdf_file.open("rb"),
	):
		result = doc.get_pdf_preview()

	decoded = base64.b64decode(result)

	assert decoded.startswith(b"%PDF")


def test_preview_folder_error():
	doc = _create_file_doc("folder")
	doc.is_folder = 1

	with pytest.raises(frappe.ValidationError):
		doc.get_pdf_preview()


def test_safe_path():
	from cloud_storage.cloud_storage.overrides.file import is_safe_path

	safe = frappe.get_site_path("public", "files", "test.txt")

	assert is_safe_path(safe)


def test_generate_sharing_link(monkeypatch):

	doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "sample.txt",
			"is_private": 0,
		}
	).insert()

	from cloud_storage.cloud_storage.overrides.file import get_sharing_link

	url = get_sharing_link(doc.name)

	assert "share?key=" in url
