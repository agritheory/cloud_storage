# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

from cloud_storage.system_packages import ensure_debian_packages

DEBIAN_PACKAGES = ["libmagic1", "libreoffice"]


def after_install():
	ensure_debian_packages(DEBIAN_PACKAGES)
