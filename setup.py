from setuptools import setup, find_packages

with open("requirements.txt") as f:
	install_requires = f.read().strip().split("\n")

# get version from __version__ variable in cloud_storage/__init__.py
from cloud_storage import __version__ as version

setup(
	name="cloud_storage",
	version=version,
	description="Frappe App for integrating with cloud storage applications",
	author="Agritheory",
	author_email="developers@agritheory.dev",
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires
)
