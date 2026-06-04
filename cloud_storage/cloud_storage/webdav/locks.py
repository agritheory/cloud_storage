# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt
# Redis-backed wsgidav LockStorage — keeps locks consistent across gunicorn workers.

from typing import Any
from collections.abc import Iterator

import frappe
from wsgidav.lock_man.lock_storage import LockStorageDict


_NAMESPACE = "webdav:lockstorage"


class _RedisDict:
	"""Minimal dict facade over a Frappe Redis hash, sufficient for LockStorageDict."""

	def __init__(self, namespace: str) -> None:
		self._ns = namespace

	def _cache(self):
		return frappe.cache()

	def __getitem__(self, key: str) -> Any:
		val = self._cache().hget(self._ns, key)
		if val is None:
			raise KeyError(key)
		return val

	def __setitem__(self, key: str, value: Any) -> None:
		self._cache().hset(self._ns, key, value)

	def __delitem__(self, key: str) -> None:
		self._cache().hdel(self._ns, key)

	def __contains__(self, key: str) -> bool:
		return self._cache().hget(self._ns, key) is not None

	def __len__(self) -> int:
		return len(self._hgetall_decoded())

	def __iter__(self) -> Iterator[str]:
		return iter(self._hgetall_decoded().keys())

	def get(self, key: str, default: Any = None) -> Any:
		val = self._cache().hget(self._ns, key)
		return default if val is None else val

	def items(self):
		return self._hgetall_decoded().items()

	def clear(self) -> None:
		self._cache().delete_value(self._ns)

	def _hgetall_decoded(self) -> dict[str, Any]:
		# hgetall unpickles values but leaves Redis hash keys as raw bytes;
		# wsgidav compares them against str URLs so we decode here.
		raw = self._cache().hgetall(self._ns) or {}
		return {(k.decode("utf-8") if isinstance(k, bytes) else k): v for k, v in raw.items()}


class RedisLockStorage(LockStorageDict):
	"""LockStorageDict backed by Redis instead of an in-process dict."""

	def __repr__(self):
		return f"RedisLockStorage(namespace={_NAMESPACE!r})"

	def open(self) -> None:
		assert self._dict is None  # type: ignore[attr-defined,has-type]
		self._dict = _RedisDict(_NAMESPACE)  # type: ignore[attr-defined]

	def close(self) -> None:
		# don't wipe Redis — other workers may still be using it
		self._dict = None  # type: ignore[assignment]
