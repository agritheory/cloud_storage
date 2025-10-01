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
	    bench --site mysite.localhost migrate-files-to-cloud
	    bench --site mysite.localhost migrate-files-to-cloud --dry-run
	    bench --site mysite.localhost migrate-files-to-cloud --limit 100
	    bench --site mysite.localhost migrate-files-to-cloud --doctype "Employee"
	    bench --site mysite.localhost migrate-files-to-cloud --older-than 30
	    bench --site mysite.localhost migrate-files-to-cloud --batch-size 50
	    bench --site mysite.localhost migrate-files-to-cloud --remove-local
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


commands = [
	migrate_files_to_cloud_storage,
]
