<!-- Copyright (c) 2025, AgriTheory and contributors
For license information, please see license.txt-->

# File Versioning and Association Logic

## S3/Cloud Storage Versioning

Versioning must be enabled at the bucket level for S3/compatible storage.

To enable file versioning, enter the following in bench console:

```ipython
In [1]: from cloud_storage.cloud_storage.overrides.file import get_cloud_storage_client
In [2]: client = get_cloud_storage_client()
In [3]: client.put_bucket_versioning(Bucket=client.bucket, VersioningConfiguration={'Status': 'Enabled'})
```

## File Versioning in Frappe/Cloud Storage App

When a file is uploaded, the backend logic manages file versions and associations as follows:

### `add_file_version`

The `add_file_version` method is called whenever a new version of a file is uploaded. It appends a new entry to the file's `versions` child table, recording:

- The version identifier (S3 VersionId)
- The user who uploaded the version
- The timestamp of the upload

This allows you to track all previous versions of a file within the File DocType.

### Handling Duplicate File Names

If a file is uploaded with a name that already exists in the system (and is not a folder):

- Instead of creating a new File document, the system updates the content, content hash, and content type of the existing File document with the new file's data.
- All associations (linked documents) are updated so that they now point to the updated File document.
- The new version is recorded in the `versions` child table using `add_file_version`.
- This ensures that all references and links in the system always point to the latest version of the file, and there are no duplicate File records for the same file name.

This logic helps maintain a single source of truth for each file name, with a complete version history and correct associations for all linked documents.
