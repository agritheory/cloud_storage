# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import json
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
	yield frappe.db


@pytest.fixture(scope="session", autouse=True)
def mock_commit(db_instance):
	"""Never commit for real against MariaDB - test isolation, the whole session's writes are
	discarded when the process exits."""
	frappe.db.commit = MagicMock()
	yield frappe.db.commit


@pytest.fixture(scope="session", autouse=True)
def reset_local_cache_index(db_instance):
	local_cache_dir = Path(frappe.get_site_path("local_cache"))
	for name in ("index.db", "index.db-wal", "index.db-shm", "index.db-journal"):
		(local_cache_dir / name).unlink(missing_ok=True)


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


@pytest.fixture(scope="session", autouse=True)
def cleanup_uploaded_files(db_instance, reset_local_cache_index):
	watched_dirs = [
		Path(frappe.get_site_path("local_cache")),
		Path(frappe.get_site_path("public", "files")),
		Path(frappe.get_site_path("private", "files")),
	]
	before = {d: (set(d.rglob("*")) if d.is_dir() else set()) for d in watched_dirs}

	yield

	for d in watched_dirs:
		after = set(d.rglob("*")) if d.is_dir() else set()
		new_paths = sorted(after - before[d], key=lambda p: len(p.parts), reverse=True)
		for path in new_paths:
			if path.is_file():
				path.unlink(missing_ok=True)
			elif path.is_dir() and not any(path.iterdir()):
				path.rmdir()


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
