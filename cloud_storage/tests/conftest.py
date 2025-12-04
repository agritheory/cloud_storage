# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

from pathlib import Path
from unittest.mock import MagicMock

import boto3
import frappe
import pytest
from frappe.utils import get_bench_path
from moto import mock_s3


def _get_logger(*args, **kwargs):
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
	frappe.logger = _get_logger

	currentsite = "test_site"
	sites = Path(get_bench_path()) / "sites"
	if (sites / "currentsite.txt").is_file():
		currentsite = (sites / "currentsite.txt").read_text()

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
		yield _MockedS3Client(client, bucket, "test_folder", 110)
