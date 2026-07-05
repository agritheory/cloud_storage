# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import json
from pathlib import Path
from unittest.mock import MagicMock

import frappe
import pytest
from botocore.exceptions import ClientError
from frappe.utils import get_bench_path
from frappe.utils.logger import get_logger as frappe_get_logger
from moto import mock_s3

MOTO_CLOUD_STORAGE_SETTINGS = {
	"access_key": "testing",
	"secret": "testing",
	"region": "us-east-1",
	"bucket": "test_bucket",
	"folder": "test_folder",
	"expiration": 110,
	"use_legacy_paths": 1,
}


def get_logger(*args, **kwargs):
	return frappe_get_logger(
		module=None,
		with_more_info=False,
		allow_site=True,
		filter=None,
		max_size=100_000,
		file_count=20,
		stream_only=True,
	)


frappe.logger = get_logger


@pytest.fixture(scope="session", autouse=True)
def db_instance():
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


@pytest.fixture(scope="session", autouse=True)
def cloud_storage_test_settings(db_instance):
	frappe.conf["cloud_storage_settings"] = dict(MOTO_CLOUD_STORAGE_SETTINGS)


@pytest.fixture(scope="session", autouse=True)
def mock_s3_backend():
	with mock_s3():
		yield


@pytest.fixture(scope="session", autouse=True)
def s3_test_bucket(db_instance, cloud_storage_test_settings, mock_s3_backend):
	from cloud_storage.cloud_storage.overrides.file import get_cloud_storage_client

	client = get_cloud_storage_client()
	try:
		client.create_bucket(Bucket=client.bucket)
	except ClientError:
		pass

	client.put_bucket_versioning(
		Bucket=client.bucket,
		VersioningConfiguration={"Status": "Enabled"},
	)
	return client


@pytest.fixture(autouse=True)
def reset_user():
	yield
	frappe.set_user("Administrator")
