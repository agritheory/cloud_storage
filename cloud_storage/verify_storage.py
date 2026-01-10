
import frappe
import os
from cloud_storage.cloud_storage.overrides.file import get_cloud_storage_client, get_cloud_storage_config
from botocore.exceptions import ClientError

def execute():
	print(f"\n{'='*50}")
	print(f"Storage Verification for Site: {frappe.local.site}")
	print(f"{'='*50}")

	# 1. Get All Files from DB
	files = frappe.get_all(
		"File",
		fields=["name", "file_name", "file_url", "is_private", "s3_key", "is_folder"],
		filters={"is_folder": 0}
	)
	total_db_files = len(files)
	print(f"Total Files in DB: {total_db_files}")

	config = get_cloud_storage_config()
	if not config:
		print("Cloud Storage not configured for this site.")
		return

	try:
		client = get_cloud_storage_client()
		bucket = client.bucket
	except Exception as e:
		print(f"Failed to initialize MinIO client: {e}")
		return

	local_count = 0
	minio_count = 0
	synced_count = 0 
	missing_local = []
	missing_minio = []

	print("\nScanning files...")

	for f in files:
		# Check Local
		if f.is_private:
			local_path = frappe.get_site_path("private", "files", f.file_name)
		else:
			local_path = frappe.get_site_path("public", "files", f.file_name)
		
		if os.path.exists(local_path):
			local_count += 1
		else:
			missing_local.append(f.file_name)

		# Check MinIO
		in_minio = False
		if f.s3_key:
			try:
				client.head_object(Bucket=bucket, Key=f.s3_key)
				in_minio = True
				minio_count += 1
				synced_count += 1
			except ClientError:
				missing_minio.append(f.file_name)
		else:
			# Not marked as migrated (s3_key is empty/null)
			pass

	print(f"\n{'='*50}")
	print(f"Verification Summary for {frappe.local.site}")
	print(f"{'='*50}")
	print(f"Total Records in DB:    {total_db_files}")
	print(f"Found Locally:          {local_count}")
	print(f"Found in MinIO (Verified): {minio_count}")
	print(f"Marked as Migrated (DB):   {len([f for f in files if f.s3_key])}")
	print(f"{'='*50}")
	
	if missing_local:
		print(f"\n[WARNING] Missing Local Files ({len(missing_local)}):")
		print(", ".join(missing_local[:10]) + ("..." if len(missing_local) > 10 else ""))

	if missing_minio:
		print(f"\n[ERROR] Missing in MinIO (DB claims migrated) ({len(missing_minio)}):")
		print(", ".join(missing_minio[:10]) + ("..." if len(missing_minio) > 10 else ""))

	# Verification of count
	if local_count != minio_count:
		print(f"\n[INFO] Count Mismatch: Local ({local_count}) vs MinIO ({minio_count})")
		print("Note: This is expected if partial migration or if 'remove_local' was used.")
	else:
		print("\n[SUCCESS] Local and MinIO counts match (for migrated files).")

	print(f"{'='*50}\n")
