# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import mimetypes
import os
from datetime import timedelta

import frappe
from botocore.exceptions import ClientError
from frappe.utils import cint, get_site_path, now_datetime

from cloud_storage.cloud_storage.overrides.file import (
	get_cloud_storage_client,
	get_file_path,
	validate_config,
	write_file,
)


def _format_bytes(size):
	for unit in ["B", "KB", "MB", "GB", "TB"]:
		if size < 1024.0:
			return f"{size:.2f} {unit}"
		size /= 1024.0
	return f"{size:.2f} PB"


def migrate_files(
	dry_run=False, limit=None, doctype=None, older_than=None, batch_size=100, remove_local=False
):
	"""
	Migrate existing local files to cloud storage.

	Args:
	    dry_run (bool): Preview migration without uploading
	    limit (int): Maximum number of files to migrate
	    doctype (str): Only migrate files attached to specific DocType
	    older_than (int): Only migrate files older than specified days
	    batch_size (int): Number of files to process per batch
	    remove_local (bool): Whether to delete local files after successful upload
	"""
	validate_config()

	config = frappe.conf.get("cloud_storage_settings")
	if config.get("use_local"):
		frappe.throw(
			"Cloud Storage is not enabled. Please set 'use_local' to 0 in 'cloud_storage_settings'."
		)

	print(f"\n{'='*80}")
	print("Cloud Storage Configuration")
	print(f"{'='*80}")
	print(f"Region:       {config.get('region')}")
	print(f"Endpoint:     {config.get('endpoint_url')}")
	print(f"Bucket:       {config.get('bucket')}")
	print(f"Folder:       {config.get('folder', '(none)')}")
	print(f"{'='*80}\n")

	filters = {
		"is_folder": 0,
		"s3_key": ["in", [None, ""]],
	}

	if doctype:
		filters["attached_to_doctype"] = doctype

	if older_than:
		cutoff_date = now_datetime() - timedelta(days=cint(older_than))
		filters["creation"] = ["<", cutoff_date]

	files = frappe.get_all(
		"File",
		filters=filters,
		fields=[
			"name",
			"file_name",
			"file_url",
			"attached_to_doctype",
			"attached_to_name",
			"file_size",
			"creation",
		],
		order_by="creation asc",
		limit=limit,
	)

	if not files:
		print("No files found to migrate.")
		return

	print(f"\n{'='*80}")
	print(f"Found {len(files)} file(s) to migrate")
	print(f"{'='*80}\n")

	if dry_run:
		print("DRY RUN MODE - No files will be uploaded\n")

	stats = {
		"total": len(files),
		"migrated": 0,
		"skipped": 0,
		"failed": 0,
		"total_size": 0,
	}

	for i in range(0, len(files), batch_size):
		batch = files[i : i + batch_size]

		for file_data in batch:
			try:
				file_doc = frappe.get_doc("File", file_data.name)
				if not file_doc.is_private:
					file_path = frappe.get_site_path("public", file_doc.file_url.lstrip("/"))
				else:
					file_path = frappe.get_site_path("private", "files", file_doc.file_name)

				if not os.path.exists(file_path):
					print(f"⚠️  SKIP: {file_doc.name} - Local file not found: {file_path}")
					stats["skipped"] += 1
					continue

				if file_doc.get("s3_key"):
					print(f"⚠️  SKIP: {file_doc.name} - Already in cloud storage (s3_key: {file_doc.s3_key})")
					stats["skipped"] += 1
					continue

				file_size = os.path.getsize(file_path)
				stats["total_size"] += file_size

				attached_to = (
					f"{file_doc.attached_to_doctype}/{file_doc.attached_to_name}"
					if file_doc.attached_to_doctype
					else "No attachment"
				)
				print(f"📄 {file_doc.name}: {file_doc.file_name} ({_format_bytes(file_size)})")
				print(f"   Attached to: {attached_to}")
				print(f"   Local path: {file_doc.file_url}")

				if not dry_run:
					with open(file_path, "rb") as f:
						file_content = f.read()

					file_doc.content = file_content
					content_type, _ = mimetypes.guess_type(file_doc.file_name)
					file_doc.content_type = content_type or "application/octet-stream"

					new_file = write_file(file_doc)
					new_file.reload()
					new_file.save()
					frappe.db.commit()

					print(
						f"   ✅ Uploaded to: {new_file.s3_key if hasattr(new_file, 's3_key') else 'cloud storage'}"
					)
					stats["migrated"] += 1

					if remove_local:
						os.remove(file_path)
						print("   🗑️  Deleted local file")
				else:
					print("   [DRY RUN] Would upload to cloud storage")
					stats["migrated"] += 1

				print()

			except Exception as e:
				print(f"❌ FAILED: {file_data.name} - {str(e)}")
				frappe.log_error(
					title=f"Cloud Storage Migration Failed: {file_data.name}", message=frappe.get_traceback()
				)
				stats["failed"] += 1
				print()

		if not dry_run:
			frappe.db.commit()

	print(f"\n{'='*80}")
	print("MIGRATION SUMMARY")
	print(f"{'='*80}")
	print(f"Total files found:     {stats['total']}")
	print(f"Successfully migrated: {stats['migrated']}")
	print(f"Skipped:               {stats['skipped']}")
	print(f"Failed:                {stats['failed']}")
	print(f"Total size:            {_format_bytes(stats['total_size'])}")

	if dry_run:
		print("\nThis was a DRY RUN. Run without --dry-run to perform actual migration.")
	else:
		print("\n✅ Migration completed!")

	print(f"{'='*80}\n")


def migrate_paths(dry_run=False, limit=None, batch_size=100):
	"""Migrate files from legacy paths to new path strategy."""
	client = get_cloud_storage_client()
	config = frappe.conf.get("cloud_storage_settings", {})

	if config.get("use_legacy_paths", True):
		print("⚠️  Legacy paths are still enabled in cloud_storage_settings.")
		print("Set 'use_legacy_paths': 0 to use the new path strategy.")
		print("This command will migrate existing files to the new strategy.\n")
		return

	print(f"\n{'='*80}")
	print("Cloud Storage Path Migration")
	print(f"{'='*80}")
	print(f"Bucket:       {client.bucket}")
	print(f"Folder:       {config.get('folder', '(none)')}")
	print(f"{'='*80}\n")

	files = frappe.get_all(
		"File",
		filters={"s3_key": ["like", "%/%/%/%"], "is_folder": 0},
		fields=["name", "file_name", "s3_key", "attached_to_doctype", "attached_to_name"],
		limit=limit,
	)

	if not files:
		print("No files found with legacy paths.")
		return

	print(f"Found {len(files)} files with legacy paths")

	if dry_run:
		print("DRY RUN MODE - No files will be moved\n")

	stats = {
		"total": len(files),
		"migrated": 0,
		"skipped": 0,
		"failed": 0,
	}

	for i in range(0, len(files), batch_size):
		batch = files[i : i + batch_size]

		for file_data in batch:
			try:
				file_doc = frappe.get_doc("File", file_data.name)
				old_path = file_data.s3_key
				new_path = get_file_path(file_doc, config.get("folder"))

				if new_path == old_path:
					print(f"⚠️  SKIP: {file_doc.name} - Path unchanged")
					stats["skipped"] += 1
					continue

				attached_to = (
					f"{file_doc.attached_to_doctype}/{file_doc.attached_to_name}"
					if file_doc.attached_to_doctype
					else "No attachment"
				)
				print(f"📄 {file_doc.name}: {file_doc.file_name}")
				print(f"   Attached to: {attached_to}")
				print(f"   Old path: {old_path}")
				print(f"   New path: {new_path}")

				if not dry_run:
					try:
						try:
							client.head_object(Bucket=client.bucket, Key=old_path)
						except ClientError as e:
							if e.response["Error"]["Code"] == "404":
								print("   ⚠️  Source file not found in S3, skipping")
								stats["skipped"] += 1
								continue
							raise

						try:
							client.head_object(Bucket=client.bucket, Key=new_path)
							print("   ⚠️  Destination already exists, skipping copy but updating record")
							file_doc.db_set("s3_key", new_path, update_modified=False)
							stats["migrated"] += 1
							print("   ✅ Updated record")
							continue
						except ClientError as e:
							if e.response["Error"]["Code"] != "404":
								raise

						copy_source = f"{client.bucket}/{client.bucket}/{old_path}"  # TODO: See in other provider than Digital Ocean
						client.copy_object(Bucket=client.bucket, CopySource=copy_source, Key=new_path)

						file_doc.db_set("s3_key", new_path, update_modified=False)
						client.delete_object(Bucket=client.bucket, Key=old_path)

						print("   ✅ Migrated")
						stats["migrated"] += 1

					except ClientError as e:
						error_code = e.response["Error"]["Code"]
						error_msg = e.response["Error"]["Message"]
						print(f"   ❌ Failed: {error_code} - {error_msg}")
						frappe.log_error(
							title=f"Cloud Storage Path Migration Failed: {file_data.name}",
							message=f"File: {file_data.name}\nOld: {old_path}\nNew: {new_path}\n\nError: {error_code}\n{error_msg}",
						)
						stats["failed"] += 1
					except Exception as e:
						print(f"   ❌ Failed: {str(e)}")
						frappe.log_error(
							title=f"Cloud Storage Path Migration Failed: {file_data.name}",
							message=f"File: {file_data.name}\nOld: {old_path}\nNew: {new_path}\n\n{frappe.get_traceback()}",
						)
						stats["failed"] += 1
				else:
					print("   [DRY RUN] Would migrate")
					stats["migrated"] += 1

				print()

			except Exception as e:
				print(f"❌ FAILED: {file_data.name} - {str(e)}")
				frappe.log_error(
					title=f"Cloud Storage Path Migration Failed: {file_data.name}", message=frappe.get_traceback()
				)
				stats["failed"] += 1
				print()

		if not dry_run:
			frappe.db.commit()

	print(f"\n{'='*80}")
	print("MIGRATION SUMMARY")
	print(f"{'='*80}")
	print(f"Total files found:     {stats['total']}")
	print(f"Successfully migrated: {stats['migrated']}")
	print(f"Skipped:               {stats['skipped']}")
	print(f"Failed:                {stats['failed']}")

	if dry_run:
		print("\nThis was a DRY RUN. Run without --dry-run to perform actual migration.")
	else:
		print("\n✅ Path migration completed!")

	print(f"{'='*80}\n")
