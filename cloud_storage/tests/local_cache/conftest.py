# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

from pathlib import Path
from unittest.mock import MagicMock

import frappe
import pytest


@pytest.fixture(scope="module", autouse=True)
def commit_runs_after_commit_callbacks(mock_commit):
	"""Scoped to this module only - the local cache admits its SQLite rows and enqueues
	replication through frappe.db.after_commit, so this module needs commit() to actually
	run that queue. Restores the plain session-wide mock afterwards, regardless of test order."""

	def fake_commit(*args, **kwargs):
		frappe.db.after_commit.run()
		frappe.db.after_commit.reset()

	frappe.db.commit = MagicMock(side_effect=fake_commit)
	yield frappe.db.commit
	frappe.db.commit = mock_commit


@pytest.fixture(scope="module", autouse=True)
def enable_local_cache(monkeymodule, patch_frappe_conf):
	# generous emergency_cache_size_gb: eviction tests below set their own tight budgets
	monkeymodule.setattr(
		"frappe.conf.cloud_storage_settings",
		{
			**frappe.conf.cloud_storage_settings,
			"local_cache_enabled": True,
			"max_cache_size_gb": 0.000005,
			"emergency_cache_size_gb": 1,
			"cache_retention_minutes": 60,
			"warm_on_read": True,
		},
	)


@pytest.fixture(scope="module")
def restricted_user():
	email = "cache_read_test_user@example.com"
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Cache Read Test",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
	return email


@pytest.fixture
def example_bytes():
	# each test appends a unique marker before uploading, to avoid write_file's hash dedup
	return (Path(__file__).parent.parent / "fixtures" / "aticonrusthex.png").read_bytes()
