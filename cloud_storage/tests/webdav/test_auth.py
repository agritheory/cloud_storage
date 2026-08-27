# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe

from cloud_storage.tests.fixtures import SHARED_VIEWER


def test_guest_gets_401_challenge_for_propfind(dav_before_request):
	frappe.set_user("Guest")
	resp = dav_before_request("PROPFIND", "/dav/", headers={"Depth": "1"})

	assert resp is not None
	assert resp.status_code == 401
	assert resp.headers.get("WWW-Authenticate", "").startswith("Basic")

	frappe.set_user("Administrator")


def test_guest_options_passes_through_without_challenge(dav_before_request):
	frappe.set_user("Guest")
	resp = dav_before_request("OPTIONS", "/dav/")

	assert resp is not None
	assert resp.status_code != 401

	frappe.set_user("Administrator")


def test_guest_copy_gets_401_before_405(dav_before_request):
	# Auth is checked before the unsupported-method rejection, so an
	# unauthenticated COPY should challenge, not reveal the method is unsupported.
	frappe.set_user("Guest")
	resp = dav_before_request("COPY", "/dav/webdav_auth_probe.txt")

	assert resp is not None
	assert resp.status_code == 401

	frappe.set_user("Administrator")


def test_copy_denied_with_405_for_authenticated_user(dav_before_request):
	frappe.set_user(SHARED_VIEWER)
	resp = dav_before_request("COPY", "/dav/webdav_auth_probe.txt")

	assert resp is not None
	assert resp.status_code == 405
	allow = resp.headers.get("Allow", "")
	assert "COPY" not in allow
	assert "PUT" in allow

	frappe.set_user("Administrator")


def test_proppatch_denied_with_405_for_authenticated_user(dav_before_request):
	frappe.set_user(SHARED_VIEWER)
	resp = dav_before_request("PROPPATCH", "/dav/webdav_auth_probe.txt")

	assert resp is not None
	assert resp.status_code == 405
	assert "PROPPATCH" not in resp.headers.get("Allow", "")

	frappe.set_user("Administrator")


def test_get_is_not_intercepted_by_before_request_hook(dav_before_request):
	# GET/HEAD/POST aren't in WEBDAV_METHODS — they're handled by
	# WebdavRenderer.render() instead, so the hook must return None (no-op)
	# rather than short-circuit the request.
	frappe.set_user(SHARED_VIEWER)
	resp = dav_before_request("GET", "/dav/webdav_auth_probe.txt")

	assert resp is None

	frappe.set_user("Administrator")


def test_non_dav_path_is_not_intercepted(dav_before_request):
	frappe.set_user("Guest")
	resp = dav_before_request("PROPFIND", "/api/method/ping")

	assert resp is None

	frappe.set_user("Administrator")
