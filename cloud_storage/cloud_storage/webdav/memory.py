# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt
# Redis-backed TTL store for macOS metadata files (._*, .DS_Store, ...).
# Acknowledges Finder's auxiliary PUTs without polluting Frappe/S3.

import frappe


_PREFIX = "webdav:osfile:"
_TTL_SECONDS = 60 * 60  # 1 hour — these are transient by nature


def _key(path: str) -> str:
	return _PREFIX + path


def get(path: str) -> bytes | None:
	return frappe.cache().get_value(_key(path))


def set(path: str, content: bytes) -> None:
	frappe.cache().set_value(_key(path), content, expires_in_sec=_TTL_SECONDS)


def delete(path: str) -> None:
	frappe.cache().delete_value(_key(path))


def contains(path: str) -> bool:
	return get(path) is not None
