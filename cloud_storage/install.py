# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import subprocess
import sys


def after_install():
	"""
	Post install script to ensure required system dependencies are present.
	Installs libmagic1 and libreoffice if not already installed.
	"""
	# Install libmagic1
	try:
		subprocess.run(
			["dpkg-query", "-W", "-f=${Status}", "libmagic1"],
			check=True,
			capture_output=True,
		)
		print("libmagic1 is already installed.")
	except subprocess.CalledProcessError:
		print("libmagic1 not found. Attempting to install...")
		try:
			subprocess.run(["sudo", "apt-get", "update"], check=True)
			subprocess.run(["sudo", "apt-get", "install", "-y", "libmagic1"], check=True)
			print("libmagic1 installed successfully.")
		except Exception as e:
			print(f"Failed to install libmagic1 automatically. Please install it manually. Error: {e}")
			sys.exit(1)

	# Install libreoffice
	try:
		subprocess.run(
			["libreoffice", "--version"],
			check=True,
			capture_output=True,
		)
		print("LibreOffice is already installed.")
	except (subprocess.CalledProcessError, FileNotFoundError):
		print("LibreOffice not found. Attempting to install...")
		try:
			subprocess.run(["sudo", "apt-get", "install", "-y", "libreoffice"], check=True)
			print("LibreOffice installed successfully.")
		except Exception as e:
			print(f"Failed to install LibreOffice automatically. Please install it manually. Error: {e}")
			sys.exit(1)
