# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import os

import frappe


def get_local_cache_path(content_hash: str) -> str:
	"""Content-addressed path under sites/{site}/local_cache/."""
	directory = frappe.get_site_path("local_cache", content_hash[:2])
	os.makedirs(directory, exist_ok=True)
	return os.path.join(directory, content_hash)
