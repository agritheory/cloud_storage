import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import frappe
import pytest
from frappe.defaults import *
from frappe.utils import get_bench_path


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
		},
	)
