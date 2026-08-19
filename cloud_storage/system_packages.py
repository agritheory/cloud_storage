# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

"""Debian/Ubuntu system package helpers for Frappe install hooks.

Used from before_install / after_install so ``bench --site … install-app``
can satisfy OS dependencies without duplicating package lists in Ansible.
Set FRAPPE_INSTALL_NONINTERACTIVE=1 (or run without a TTY) for automation.
"""

from __future__ import annotations

import os
import subprocess
import sys

ENV_NONINTERACTIVE = "FRAPPE_INSTALL_NONINTERACTIVE"


def is_noninteractive() -> bool:
	return os.environ.get(ENV_NONINTERACTIVE) == "1" or not sys.stdin.isatty()


def can_sudo() -> bool:
	if os.geteuid() == 0:
		return True
	return (
		subprocess.run(
			["sudo", "-n", "true"],
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
		).returncode
		== 0
	)


def dpkg_installed(package: str) -> bool:
	return (
		subprocess.run(
			["dpkg", "-s", package],
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
		).returncode
		== 0
	)


def ensure_debian_packages(packages: list[str], *, required: bool = True) -> None:
	"""Install missing Debian packages via apt when sudo is available."""
	if sys.platform != "linux":
		message = f"Install system packages manually: {', '.join(packages)}"
		if required:
			raise RuntimeError(message)
		print(message)
		return

	missing = [pkg for pkg in packages if not dpkg_installed(pkg)]
	if not missing:
		return

	if not can_sudo():
		message = (
			f"Need passwordless sudo to install: {', '.join(missing)}. "
			f"Run: sudo apt-get install -y {' '.join(missing)}"
		)
		if required or is_noninteractive():
			raise RuntimeError(message)
		print(message)
		return

	env = os.environ.copy()
	env["DEBIAN_FRONTEND"] = "noninteractive"
	subprocess.run(["sudo", "apt-get", "update"], check=required, env=env)
	subprocess.run(
		["sudo", "apt-get", "install", "-y", *missing],
		check=required,
		env=env,
	)
