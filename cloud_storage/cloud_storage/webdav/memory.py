# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt
# Redis-backed TTL store for macOS metadata files (._*, .DS_Store, ...).
# Acknowledges Finder's auxiliary PUTs without polluting Frappe/S3.

import frappe


PREFIX = "webdav:osfile:"
TTL_SECONDS = 60 * 60  # 1 hour — these are transient by nature


def cache_key(path: str) -> str:
	return PREFIX + path


def get(path: str) -> bytes | None:
	return frappe.cache().get_value(cache_key(path))


def set(path: str, content: bytes) -> None:
	frappe.cache().set_value(cache_key(path), content, expires_in_sec=TTL_SECONDS)


def delete(path: str) -> None:
	frappe.cache().delete_value(cache_key(path))


def contains(path: str) -> bool:
	return get(path) is not None
