# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import click
import frappe
from frappe.commands import pass_context


@click.command("migrate-files-to-cloud-storage")
@click.option("--site", help="Site name")
@click.option(
	"--dry-run",
	is_flag=True,
	default=False,
	help="Preview migration without actually uploading files",
)
@click.option("--limit", type=int, help="Limit number of files to migrate")
@click.option("--doctype", help="Only migrate files attached to specific DocType")
@click.option("--older-than", help="Only migrate files older than specified days")
@click.option(
	"--batch-size", type=int, default=100, help="Number of files to process in each batch"
)
@click.option(
	"--remove-local", is_flag=True, default=False, help="Remove local files after migration"
)
@pass_context
def migrate_files_to_cloud_storage(
	context,
	site=None,
	dry_run=False,
	limit=None,
	doctype=None,
	older_than=None,
	batch_size=100,
	remove_local=False,
):
	"""
	Migrate existing local files to cloud storage.

	Examples:
	    bench --site mysite.localhost migrate-files-to-cloud-storage
	    bench --site mysite.localhost migrate-files-to-cloud-storage --dry-run
	    bench --site mysite.localhost migrate-files-to-cloud-storage --limit 100
	    bench --site mysite.localhost migrate-files-to-cloud-storage --doctype "Employee"
	    bench --site mysite.localhost migrate-files-to-cloud-storage --older-than 30
	    bench --site mysite.localhost migrate-files-to-cloud-storage --batch-size 50
	    bench --site mysite.localhost migrate-files-to-cloud-storage --remove-local
	"""
	site = site or context.sites[0]
	frappe.init(site=site)
	frappe.connect()

	try:
		from cloud_storage.migration import migrate_files

		migrate_files(
			dry_run=dry_run,
			limit=limit,
			doctype=doctype,
			older_than=older_than,
			batch_size=batch_size,
			remove_local=remove_local,
		)
	finally:
		frappe.destroy()


@click.command("migrate-cloud-storage-paths")
@click.option("--site", help="Site name")
@click.option("--dry-run", is_flag=True, default=False, help="Preview migration")
@click.option("--limit", type=int, help="Limit number of files")
@click.option(
	"--batch-size", type=int, help="Number of files to process in each batch", default=100
)
@pass_context
def migrate_cloud_storage_paths(context, site=None, dry_run=False, limit=None, batch_size=100):
	"""
	Migrate existing cloud storage files from legacy paths to new strategy.

	Examples:
	    bench --site mysite migrate-cloud-storage-paths --dry-run
	    bench --site mysite migrate-cloud-storage-paths --limit 100
	    bench --site mysite migrate-cloud-storage-paths --batch-size 100
	"""
	site = site or context.sites[0]
	frappe.init(site=site)
	frappe.connect()

	try:
		from cloud_storage.migration import migrate_paths

		migrate_paths(dry_run=dry_run, limit=limit, batch_size=batch_size)
	finally:
		frappe.destroy()


@click.command("webdav-server")
@click.option("--site", help="Site name")
@click.option("--port", type=int, default=8010, help="Port to listen on (default: 8010)")
@click.option("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
@pass_context
def webdav_server(context, site=None, port=8010, host="0.0.0.0"):
	"""
	Start a WebDAV server for rclone / desktop client integration.

	Files are served read-only from the Frappe File tree. Authenticate with
	a Frappe API key and secret (Settings → My Account → API Access).

	Examples:
	    bench --site mysite.localhost webdav-server
	    bench --site mysite.localhost webdav-server --port 8010

	rclone config (add to ~/.config/rclone/rclone.conf):
	    [frappe]
	    type = webdav
	    url = http://localhost:8010/
	    vendor = other
	    user = <api_key>
	    pass = <api_secret (run: rclone obscure <secret>)>
	"""
	from wsgiref.simple_server import make_server

	from cloud_storage.cloud_storage.webdav.app import create_webdav_app

	site = site or context.sites[0]
	app = create_webdav_app(site=site)

	print(f"WebDAV server running on http://{host}:{port}/")
	print(f"Site: {site}")
	print()
	print("rclone config snippet:")
	print("  [frappe]")
	print("  type = webdav")
	print(f"  url = http://localhost:{port}/")
	print("  vendor = other")
	print("  user = <your Frappe api_key>")
	print("  pass = <rclone obscure <your api_secret>>")

	httpd = make_server(host, port, app)
	httpd.serve_forever()


commands = [
	migrate_files_to_cloud_storage,
	migrate_cloud_storage_paths,
	webdav_server,
]
