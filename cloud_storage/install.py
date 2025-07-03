# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import os
import subprocess
from getpass import getpass
from sys import platform


def is_root():
	return os.geteuid() == 0


def test_sudo():
	args = "sudo -S echo OK".split()
	kwargs = dict(stdout=subprocess.PIPE, encoding="ascii")
	cmd = subprocess.run(args, **kwargs)
	return "OK" in cmd.stdout


def install(module, pwd=""):
	args = f"sudo -S apt-get -y install {module}".split()
	kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="ascii")
	if pwd:
		kwargs.update(input=pwd)
	cmd = subprocess.run(args, **kwargs)
	return cmd.stdout, cmd.stderr


def after_install():
	modules = ["libmagic1", "libreoffice"]
	if platform != "linux":
		print(f"You need to manually install the following modules: {', '.join(modules)}.")
		return

	has_sudo_permissions = is_root() or test_sudo()
	pwd = ""
	if not has_sudo_permissions:
		pwd = getpass(f"Provide sudo password to install {', '.join(modules)}: ")

	for module in modules:
		try:
			out, err = install(module, pwd)
			if err:
				print(f"There was an error installing {module}: {err}.")
			if out:
				print(f"{module}: {out}")
		except Exception as e:
			print(f"There was an error installing {module}: {e}.")
