# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import boto3
import frappe
import pytest
from frappe.utils import get_bench_path
from moto import mock_s3


def test_logger(*args, **kwargs):
	from frappe.utils.logger import get_logger

	return get_logger(
		module=None,
		with_more_info=False,
		allow_site=True,
		filter=None,
		max_size=100_000,
		file_count=20,
		stream_only=True,
	)


class _MockedS3Client:
	"""Wrapper around boto3 S3 client to add custom attributes"""

	def __init__(self, client, bucket, folder, expiration):
		self._client = client
		self.bucket = bucket
		self.folder = folder
		self.expiration = expiration

	def __getattr__(self, attr):
		return getattr(self._client, attr)


@pytest.fixture(scope="module")
def monkeymodule():
	with pytest.MonkeyPatch.context() as mp:
		yield mp


@pytest.fixture(scope="session", autouse=True)
def db_instance():
	frappe.logger = test_logger

	sites = Path(get_bench_path()) / "sites"
	currentsite = "test_site"
	if (sites / "currentsite.txt").is_file():
		currentsite = (sites / "currentsite.txt").read_text().strip()
	else:
		common_config = sites / "common_site_config.json"
		if common_config.is_file():
			config = json.loads(common_config.read_text())
			currentsite = config.get("default_site", currentsite)

	frappe.init(site=currentsite, sites_path=sites)
	frappe.connect()
	frappe.db.commit = MagicMock()
	yield frappe.db


@pytest.fixture(scope="module", autouse=True)
def patch_frappe_conf(monkeymodule):
	monkeymodule.setattr(
		"frappe.conf.cloud_storage_settings",
		{
			"access_key": "test",
			"secret": "test_secret",
			"region": "us-east-1",
			"bucket": "test_bucket",
			"endpoint_url": "https://test.imgainarys3.edu",
			"expiration": 110,
			"folder": "test_folder",
			"use_legacy_paths": 1,
		},
	)


@pytest.fixture
def mocked_s3_client():
	with mock_s3():
		client = boto3.client(
			"s3",
			region_name="us-east-1",
			aws_access_key_id="testing",
			aws_secret_access_key="testing",
		)
		bucket = "test_bucket"
		client.create_bucket(Bucket=bucket)
		client.put_bucket_versioning(
			Bucket=bucket,
			VersioningConfiguration={"Status": "Enabled"},
		)
		yield _MockedS3Client(client, bucket, "test_folder", 110)


@pytest.fixture
def local_storage():
	old = getattr(frappe.conf, "cloud_storage_settings", None)
	frappe.conf.cloud_storage_settings = {"use_local": True}
	yield
	frappe.conf.cloud_storage_settings = old
	frappe.set_user("Administrator")


@pytest.fixture
def dav_request():
	def send_dav_request(method: str, path: str, data: bytes = b"", headers: dict | None = None):
		from werkzeug.test import EnvironBuilder

		from cloud_storage.cloud_storage.webdav.renderer import invoke_webdav

		builder = EnvironBuilder(method=method, path=path, data=data, headers=headers or {})
		return invoke_webdav(builder.get_request())

	return send_dav_request


@pytest.fixture
def dav_before_request():
	"""Drive the real handle_webdav_methods before_request hook, not invoke_webdav directly."""

	def send_before_request(method: str, path: str, data: bytes = b"", headers: dict | None = None):
		from werkzeug.test import EnvironBuilder

		from cloud_storage.cloud_storage.webdav.renderer import WebdavResponse, handle_webdav_methods

		builder = EnvironBuilder(method=method, path=path, data=data, headers=headers or {})
		frappe.local.request = builder.get_request()
		try:
			handle_webdav_methods()
			return None
		except WebdavResponse as raised:
			return raised.get_response()

	return send_before_request


@pytest.fixture
def track_files():
	"""Register File docs for teardown cleanup that runs even on assertion failure."""
	created = []
	disk_paths = set()

	def track(name):
		created.append(name)
		if name and frappe.db.exists("File", name):
			doc = frappe.get_doc("File", name)
			if not doc.is_folder:
				disk_paths.add(doc.get_full_path())
		return name

	yield track

	frappe.set_user("Administrator")
	# reversed: a folder registered before its children would hit FolderNotEmpty
	for name in reversed(created):
		if name and frappe.db.exists("File", name):
			frappe.delete_doc("File", name, force=True, ignore_permissions=True)
	# Some flows (e.g. MOVE onto an existing destination) detach a source
	# doc's file_url before deleting it, so on_trash never unlinks the disk
	# copy — sweep paths captured at registration time as a backstop.
	for path in disk_paths:
		if path and os.path.exists(path):
			os.remove(path)
