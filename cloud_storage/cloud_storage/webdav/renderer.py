# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""
Serve WebDAV from inside Frappe (no second port).

Two pieces:
  * WebdavRenderer       — page_renderer for GET/HEAD/POST (Frappe routes these
                           naturally into get_response()).
  * handle_webdav_methods — before_request hook for WebDAV methods Frappe's
                           app.py would otherwise reject with NotFound
                           (PROPFIND, MKCOL, MOVE, COPY, LOCK, UNLOCK, PUT,
                           DELETE, OPTIONS). We short-circuit by raising
                           WebdavResponse — Frappe's top-level
                           `except HTTPException` returns it.

Both paths invoke the same embedded WsgiDAVApp, mounted at /dav/.
"""

from __future__ import annotations

import io
import threading
import time

import frappe
from frappe.auth import validate_auth
from werkzeug.exceptions import HTTPException
from werkzeug.wrappers import Response


def _log(msg: str) -> None:
	"""Direct-to-stdout logger so we see things even mid-request."""
	print(f"[DAV {time.strftime('%H:%M:%S')}] {msg}", flush=True)


_WEBDAV_METHODS = {
	"PROPFIND",
	"PROPPATCH",
	"MKCOL",
	"COPY",
	"MOVE",
	"LOCK",
	"UNLOCK",
	"OPTIONS",
	"PUT",
	"DELETE",
}

_MOUNT_PREFIX = "/dav"

_webdav_app = None
_webdav_app_lock = threading.Lock()


class WebdavResponse(HTTPException):
	"""Carry a Werkzeug Response out of a before_request hook."""

	def __init__(self, response: Response) -> None:
		super().__init__()
		self._response = response
		self.code = response.status_code

	def get_response(self, environ=None):
		return self._response


class WebdavRenderer:
	def __init__(self, path: str, http_status_code: int | None = None) -> None:
		self.path = path  # PathResolver strips leading/trailing slashes
		self.http_status_code = http_status_code or 200

	def can_render(self) -> bool:
		return self.path == "dav" or self.path.startswith("dav/")

	def render(self) -> Response:
		request = frappe.local.request
		if _needs_auth_challenge(request):
			return _auth_challenge()
		return _invoke_webdav(request)


def handle_webdav_methods() -> None:
	"""before_request hook: intercept WebDAV methods on /dav/* paths.

	Runs inside init_request() before app.py's `request.method in (GET,HEAD,POST)`
	check, so PROPFIND etc. can be handled without app.py raising NotFound.
	"""
	request = getattr(frappe.local, "request", None)
	if not request or not request.path.startswith(_MOUNT_PREFIX):
		return
	if request.method not in _WEBDAV_METHODS:
		return

	# OPTIONS is a capability probe — let it through without auth so unmounted
	# clients can discover us. Every other method needs a real user.
	if request.method != "OPTIONS":
		validate_auth()
		if _needs_auth_challenge(request):
			_log(f"{request.method} {request.path} → 401 (no auth, requesting credentials)")
			raise WebdavResponse(_auth_challenge())

	resp = _invoke_webdav(request)
	_log(f"{request.method} {request.path} → {resp.status_code} user={frappe.session.user}")
	raise WebdavResponse(resp)


def _needs_auth_challenge(request) -> bool:
	"""True when we should respond with 401 to make the client send credentials."""
	if frappe.session.user != "Guest":
		return False
	# Some clients (macOS Finder) silently mount as Guest if any operation
	# succeeds anonymously. Force a Basic challenge whenever Guest tries
	# anything other than OPTIONS so the credentials get cached + reused.
	return request.method != "OPTIONS"


def _auth_challenge() -> Response:
	resp = Response("Authentication required\n", status=401, mimetype="text/plain")
	resp.headers["WWW-Authenticate"] = 'Basic realm="Frappe Cloud Storage"'
	return resp


def _get_webdav_app():
	"""Build the WsgiDAVApp once per process (thread-safe lazy init)."""
	global _webdav_app
	if _webdav_app is not None:
		return _webdav_app
	with _webdav_app_lock:
		if _webdav_app is not None:
			return _webdav_app
		from wsgidav.dc.base_dc import BaseDomainController
		from wsgidav.lock_man.lock_storage import LockStorageDict
		from wsgidav.wsgidav_app import WsgiDAVApp

		from cloud_storage.cloud_storage.webdav.provider import FrappeDAVProvider

		class _NoAuthDC(BaseDomainController):
			"""wsgidav's auth is a no-op here — Frappe already authenticated."""

			def get_domain_realm(self, path_info, environ):
				return "Frappe"

			def require_authentication(self, realm, environ):
				return False

			def supports_http_digest_auth(self):
				return False

			def basic_auth_user(self, realm, user_name, password, environ):
				return True

			def digest_auth_user(self, realm, user_name, environ):
				raise NotImplementedError

		config = {
			"provider_mapping": {"/": FrappeDAVProvider()},
			"http_authenticator": {
				"domain_controller": _NoAuthDC,
				# wsgidav refuses to init with both off — keep basic on, but
				# require_authentication=False above means it's never invoked.
				"accept_basic": True,
				"accept_digest": False,
				"default_to_digest": False,
			},
			"lock_storage": LockStorageDict(),
			"verbose": 1,
			"logging": {"enable_loggers": []},
		}
		_webdav_app = WsgiDAVApp(config)
		return _webdav_app


def _invoke_webdav(request) -> Response:
	"""Run the embedded WsgiDAVApp with request's environ, return its Response."""
	environ = dict(request.environ)
	# Mount wsgidav under /dav/ — strip the prefix from PATH_INFO and append it
	# to SCRIPT_NAME so wsgidav-generated URLs (href, etc.) include /dav/.
	path_info = environ.get("PATH_INFO", "/")
	if path_info.startswith(_MOUNT_PREFIX):
		environ["PATH_INFO"] = path_info[len(_MOUNT_PREFIX):] or "/"
		environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + _MOUNT_PREFIX

	# Pre-read the body via werkzeug (which handles bounded reads & chunked TE
	# properly) and replace wsgi.input with an in-memory BytesIO. wsgidav
	# otherwise tries to read directly from the raw socket — which is where
	# wsgiref/werkzeug-dev block waiting on bytes that buffered clients
	# (Finder) don't flush until the response starts.
	body = request.get_data()
	environ["wsgi.input"] = io.BytesIO(body)
	environ["CONTENT_LENGTH"] = str(len(body))
	environ.pop("HTTP_TRANSFER_ENCODING", None)

	status_holder = []
	headers_holder = []

	def start_response(status, headers, exc_info=None):
		status_holder.append(status)
		headers_holder[:] = headers
		return lambda data: None

	app = _get_webdav_app()
	body_iter = app(environ, start_response)
	try:
		out = b"".join(body_iter)
	finally:
		if hasattr(body_iter, "close"):
			body_iter.close()

	status_code = int(status_holder[0].split(" ", 1)[0]) if status_holder else 500
	return Response(out, status=status_code, headers=headers_holder)
