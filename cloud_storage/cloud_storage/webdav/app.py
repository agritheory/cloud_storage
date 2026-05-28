# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from wsgidav.dc.base_dc import BaseDomainController
from wsgidav.lock_man.lock_storage import LockStorageDict
from wsgidav.wsgidav_app import WsgiDAVApp

from .provider import FrappeDAVProvider


class FrappeDomainController(BaseDomainController):
	"""Validates rclone Basic Auth credentials against Frappe's api_key / api_secret."""

	def get_domain_realm(self, path_info: str, environ) -> str:
		return "Frappe Cloud Storage"

	def require_authentication(self, realm: str, environ) -> bool:
		return True

	def supports_http_digest_auth(self) -> bool:
		return False

	def digest_auth_user(self, realm: str, user_name: str, environ: dict) -> str:
		raise NotImplementedError

	def basic_auth_user(self, realm: str, user_name: str, password: str, environ: dict) -> bool:
		"""user_name is the Frappe api_key, password is the api_secret."""
		try:
			user = frappe.db.get_value("User", {"api_key": user_name}, "name")
			if not user:
				return False
			from frappe.utils.password import get_decrypted_password

			stored_secret = get_decrypted_password("User", user, fieldname="api_secret")
			if password == stored_secret:
				frappe.set_user(user)
				return True
			return False
		except Exception:
			frappe.logger().exception("WebDAV basic auth error")
			return False


class _LimitedInput:
	"""Wraps wsgi.input so that reads stop at CONTENT_LENGTH bytes.

	wsgiref passes the raw socket as wsgi.input without limiting it to
	Content-Length. wsgidav's _stream_data reads in a loop until read()
	returns b"" — on a live HTTP/1.1 connection the socket never returns b""
	after the body, so the PUT handler blocks forever and never sends a response.
	"""

	def __init__(self, raw, length: int) -> None:
		self._raw = raw
		self._remaining = length

	def read(self, size: int = -1) -> bytes:
		if self._remaining <= 0:
			return b""
		n = self._remaining if size < 0 else min(size, self._remaining)
		data = self._raw.read(n)
		self._remaining -= len(data)
		return data

	def readline(self, size: int = -1) -> bytes:
		if self._remaining <= 0:
			return b""
		n = self._remaining if size < 0 else min(size, self._remaining)
		data = self._raw.readline(n)
		self._remaining -= len(data)
		return data


class _ChunkedInput:
	"""Decodes HTTP/1.1 Transfer-Encoding: chunked from wsgi.input.

	Required for macOS Finder, which sends large PUT bodies as chunked without
	a Content-Length header. wsgiref doesn't decode chunks, so without this the
	raw chunk framing ends up in the body and reads past the last chunk block.

	Uses a socket timeout to recover when Finder buffers its final small chunk
	and the trailing CRLF / 0-terminator never arrives: by then we have every
	byte of the file body, and treating the timeout as EOF lets the PUT complete.
	"""

	_IDLE_TIMEOUT = 5.0

	def __init__(self, raw) -> None:
		self._raw = raw
		self._chunk_remaining = 0
		self._eof = False
		# Find the underlying socket so we can apply a per-read timeout.
		probe = raw
		while hasattr(probe, "raw"):
			probe = probe.raw
		self._sock = getattr(probe, "_sock", None)

	def _readline(self) -> bytes:
		if self._sock is not None:
			old = self._sock.gettimeout()
			self._sock.settimeout(self._IDLE_TIMEOUT)
			try:
				return self._raw.readline()
			except (TimeoutError, OSError):
				return b""
			finally:
				self._sock.settimeout(old)
		return self._raw.readline()

	def _read(self, n: int) -> bytes:
		if self._sock is not None:
			old = self._sock.gettimeout()
			self._sock.settimeout(self._IDLE_TIMEOUT)
			try:
				return self._raw.read(n)
			except (TimeoutError, OSError):
				return b""
			finally:
				self._sock.settimeout(old)
		return self._raw.read(n)

	def read(self, size: int = -1) -> bytes:
		if self._eof:
			return b""
		out = bytearray()
		while size < 0 or len(out) < size:
			if self._chunk_remaining == 0:
				line = self._readline()
				if not line:
					self._eof = True
					break
				size_str = line.split(b";", 1)[0].strip()
				try:
					self._chunk_remaining = int(size_str, 16)
				except ValueError:
					self._eof = True
					break
				if self._chunk_remaining == 0:
					while True:
						trailer = self._readline()
						if trailer in (b"\r\n", b"\n", b""):
							break
					self._eof = True
					break
			to_read = self._chunk_remaining
			if size >= 0:
				to_read = min(to_read, size - len(out))
			data = self._read(to_read)
			if not data:
				self._eof = True
				break
			out.extend(data)
			self._chunk_remaining -= len(data)
			if self._chunk_remaining == 0:
				self._readline()  # CRLF after chunk data (may time out — that's OK)
		return bytes(out)

	def readline(self, size: int = -1) -> bytes:
		out = bytearray()
		while size < 0 or len(out) < size:
			byte = self.read(1)
			if not byte:
				break
			out.extend(byte)
			if byte == b"\n":
				break
		return bytes(out)


class FrappeInitMiddleware:
	"""Wraps the WebDAV WSGI app to initialise / tear down Frappe's per-request context.

	WsgiDAVApp.__call__ is a generator (it uses ``yield from`` internally). If we call
	it and return the generator object, the body won't run until wsgiref iterates it —
	which is after our finally-block has called frappe.destroy(). Making this middleware
	also a generator (via ``yield from``) keeps frappe alive for the entire response.
	"""

	def __init__(self, app, site: str) -> None:
		self.app = app
		self.site = site

	def __call__(self, environ: dict, start_response):
		method = environ.get("REQUEST_METHOD", "?")
		path = environ.get("PATH_INFO", "?")
		cl_raw = environ.get("CONTENT_LENGTH", "MISSING")
		te = environ.get("HTTP_TRANSFER_ENCODING", "")
		print(f"[REQ] {method} {path!r} CL={cl_raw!r} TE={te!r}", flush=True)
		frappe.init(site=self.site)
		frappe.connect()
		# Wrap wsgi.input — wsgiref passes the raw socket without honouring
		# Content-Length or decoding chunked transfer encoding, so any read past
		# the body blocks forever waiting on the still-open connection.
		if environ.get("HTTP_TRANSFER_ENCODING", "").lower() == "chunked":
			environ["wsgi.input"] = _ChunkedInput(environ["wsgi.input"])
		else:
			cl = int(environ.get("CONTENT_LENGTH") or 0)
			environ["wsgi.input"] = _LimitedInput(environ["wsgi.input"], cl)
		try:
			yield from self.app(environ, start_response)
		finally:
			print(f"[DONE] {method} {path!r}", flush=True)
			frappe.destroy()


def create_webdav_app(site: str) -> FrappeInitMiddleware:
	"""Return a WSGI application serving the Frappe File tree over WebDAV."""
	provider = FrappeDAVProvider()
	config = {
		"provider_mapping": {"/": provider},
		"http_authenticator": {
			"domain_controller": FrappeDomainController,
			"accept_basic": True,
			"accept_digest": False,
			"default_to_digest": False,
		},
		"lock_storage": LockStorageDict(),
		"verbose": 1,
		"logging": {
			"enable_loggers": [],
		},
	}
	webdav_app = WsgiDAVApp(config)
	return FrappeInitMiddleware(webdav_app, site)
