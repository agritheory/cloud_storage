# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""
Serve WebDAV from inside Frappe.

Two pieces:
  * WebdavRenderer       — page_renderer for GET/HEAD/POST (Frappe routes these
                           naturally into get_response()).
  * handle_webdav_methods — before_request hook for WebDAV methods Frappe's
                           app.py would otherwise reject with NotFound
                           (PROPFIND, MKCOL, MOVE, LOCK, UNLOCK, PUT, DELETE,
                           OPTIONS). COPY and PROPPATCH are explicitly rejected.
                           We short-circuit by raising WebdavResponse — Frappe's top-level
                           `except HTTPException` returns it.

Both paths invoke the same embedded WsgiDAVApp, mounted at /dav/.
"""

import io
import threading

import frappe
from frappe.auth import validate_auth
from werkzeug.exceptions import HTTPException
from werkzeug.wrappers import Response
from wsgidav.dc.base_dc import BaseDomainController
from wsgidav.wsgidav_app import WsgiDAVApp

from cloud_storage.cloud_storage.webdav.locks import RedisLockStorage
from cloud_storage.cloud_storage.webdav.provider import FrappeDAVProvider


logger = frappe.logger("webdav", allow_site=False)


WEBDAV_METHODS = {
	"PROPFIND",
	"MKCOL",
	"MOVE",
	"LOCK",
	"UNLOCK",
	"OPTIONS",
	"PUT",
	"DELETE",
}
UNSUPPORTED_WEBDAV_METHODS = {"COPY", "PROPPATCH"}

MOUNT_PREFIX = "/dav"

webdav_app = None
webdav_app_lock = threading.Lock()


class WebdavResponse(HTTPException):
	"""Carry a Werkzeug Response out of a before_request hook."""

	def __init__(self, response: Response) -> None:  # noqa: B042
		super().__init__(response=response)
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
		if needs_auth_challenge(request):
			return auth_challenge()
		return invoke_webdav(request)


class NoAuthDC(BaseDomainController):
	"""WsgiDAV auth adapter; Frappe has already authenticated the request."""

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


def handle_webdav_methods() -> None:
	"""before_request hook: intercept WebDAV methods on /dav/* paths.

	Runs inside init_request() before app.py's `request.method in (GET,HEAD,POST)`
	check, so PROPFIND etc. can be handled without app.py raising NotFound.
	"""
	request = getattr(frappe.local, "request", None)
	if not request or not request.path.startswith(MOUNT_PREFIX):
		return
	if request.method in UNSUPPORTED_WEBDAV_METHODS:
		validate_auth()
		if needs_auth_challenge(request):
			raise WebdavResponse(auth_challenge())
		raise WebdavResponse(method_not_allowed(request.method))
	if request.method not in WEBDAV_METHODS:
		return

	# OPTIONS is a capability probe — let it through without auth so unmounted
	# clients can discover us. Every other method needs a real user.
	if request.method != "OPTIONS":
		validate_auth()
		if needs_auth_challenge(request):
			logger.debug(f"{request.method} {request.path} → 401 (no auth)")
			raise WebdavResponse(auth_challenge())

	resp = invoke_webdav(request)
	logger.debug(f"{request.method} {request.path} → {resp.status_code} user={frappe.session.user}")
	raise WebdavResponse(resp)


def needs_auth_challenge(request) -> bool:
	"""True when we should respond with 401 to make the client send credentials."""
	if frappe.session.user != "Guest":
		return False
	# Some clients (macOS Finder) silently mount as Guest if any operation
	# succeeds anonymously. Force a Basic challenge whenever Guest tries
	# anything other than OPTIONS so the credentials get cached + reused.
	return request.method != "OPTIONS"


def auth_challenge() -> Response:
	resp = Response("Authentication required\n", status=401, mimetype="text/plain")
	resp.headers["WWW-Authenticate"] = 'Basic realm="Frappe Cloud Storage"'
	return resp


def method_not_allowed(method: str) -> Response:
	resp = Response(f"{method} is not supported\n", status=405, mimetype="text/plain")
	resp.headers["Allow"] = ", ".join(sorted(WEBDAV_METHODS))
	return resp


def get_webdav_app():
	"""Build the WsgiDAVApp once per process (thread-safe lazy init)."""
	global webdav_app
	if webdav_app is not None:
		return webdav_app
	with webdav_app_lock:
		if webdav_app is not None:
			return webdav_app
		config = {
			"provider_mapping": {MOUNT_PREFIX: FrappeDAVProvider()},
			"http_authenticator": {
				"domain_controller": NoAuthDC,
				"accept_basic": True,
				"accept_digest": False,
				"default_to_digest": False,
			},
			"lock_storage": RedisLockStorage(),
			"verbose": 1,
			"logging": {"enable_loggers": []},
		}
		webdav_app = WsgiDAVApp(config)
		return webdav_app


def invoke_webdav(request) -> Response:
	"""Run the embedded WsgiDAVApp with request's environ, return its Response."""
	environ = dict(request.environ)
	# wsgidav's provider is mounted at /dav, so it handles PATH_INFO/SCRIPT_NAME
	# routing itself. We just pass the environ through unchanged.

	# Pre-read the body via werkzeug (which handles bounded reads & chunked
	# Transfer-Encoding properly) and replace wsgi.input with an in-memory BytesIO. wsgidav
	# otherwise tries to read directly from the raw socket — which is where
	# wsgiref/werkzeug-dev block waiting on bytes that buffered clients
	# (Finder) don't flush until the response starts.
	body = request.get_data()
	environ["wsgi.input"] = io.BytesIO(body)
	environ["CONTENT_LENGTH"] = str(len(body))
	environ.pop("HTTP_TRANSFER_ENCODING", None)

	status_holder: list[str] = []
	headers_holder: list[tuple[str, str]] = []

	def start_response(status, headers, exc_info=None):
		status_holder.append(status)
		headers_holder[:] = headers
		return lambda data: None

	app = get_webdav_app()
	body_iter = None
	out = b""
	status_code = 500
	try:
		body_iter = app(environ, start_response)
		out = b"".join(body_iter)
	finally:
		if body_iter is not None and hasattr(body_iter, "close"):
			body_iter.close()

	status_code = int(status_holder[0].split(" ", 1)[0]) if status_holder else 500
	resp = Response(out, status=status_code, headers=headers_holder)
	if request.method == "OPTIONS" and resp.headers.get("Allow"):
		allowed = [
			m.strip()
			for m in resp.headers["Allow"].split(",")
			if m.strip() not in UNSUPPORTED_WEBDAV_METHODS
		]
		resp.headers["Allow"] = ", ".join(allowed)
	return resp
