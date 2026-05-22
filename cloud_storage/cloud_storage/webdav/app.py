# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from wsgidav.dc.base_dc import BaseDomainController
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
		frappe.init(site=self.site)
		frappe.connect()
		try:
			yield from self.app(environ, start_response)
		finally:
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
		"verbose": 1,
		"logging": {
			"enable_loggers": [],
		},
	}
	webdav_app = WsgiDAVApp(config)
	return FrappeInitMiddleware(webdav_app, site)
