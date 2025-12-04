# Cloud Storage Migration

This guide covers migrating existing local files to cloud storage and updating file paths to the new path strategy.

## Prerequisites

Before running migration commands, ensure:

1. Cloud storage is properly configured in `site_config.json`
2. You have tested the configuration with new file uploads
3. You have a backup of your files (recommended)

## Migrating Local Files to Cloud Storage

The `migrate-files-to-cloud-storage` command uploads existing local files to your configured cloud storage provider.

### Basic Usage

```bash
bench --site mysite migrate-files-to-cloud-storage
```

### Options

| Option | Type | Description |
|--------|------|-------------|
| `--dry-run` | flag | Preview migration without uploading files |
| `--limit` | integer | Maximum number of files to migrate |
| `--doctype` | string | Only migrate files attached to specific DocType |
| `--older-than` | integer | Only migrate files older than specified days |
| `--batch-size` | integer | Number of files to process per batch (default: 100) |
| `--remove-local` | flag | Remove local files after successful upload |

### Examples

**Preview migration:**

```bash
bench --site mysite migrate-files-to-cloud-storage --dry-run
```

**Migrate specific number of files:**

```bash
bench --site mysite migrate-files-to-cloud-storage --limit 100
```

**Migrate files for specific DocType:**

```bash
bench --site mysite migrate-files-to-cloud-storage --doctype "Employee"
```

**Migrate only old files:**

```bash
bench --site mysite migrate-files-to-cloud-storage --older-than 30
```

**Migrate with custom batch size:**

```bash
bench --site mysite migrate-files-to-cloud-storage --batch-size 50
```

**Migrate and remove local files:**

```bash
bench --site mysite migrate-files-to-cloud-storage --remove-local
```

> **Warning:** Use `--remove-local` only after verifying successful uploads. Deleted files cannot be recovered.

### Migration Process

The migration script:

1. Finds all files with no `s3_key` (not yet in cloud storage)
2. Applies filters (DocType, age, limit)
3. For each file:
   - Reads the local file
   - Uploads to cloud storage
   - Updates the File document with `s3_key`
   - Optionally removes the local file
4. Commits changes in batches
5. Shows summary statistics

### Output Example

```
================================================================================
Cloud Storage Configuration
================================================================================
Region:       us-east-1
Endpoint:     https://nyc3.digitaloceanspaces.com
Bucket:       my-bucket
Folder:       frappe-files
================================================================================

================================================================================
Found 250 file(s) to migrate
================================================================================

📄 abc123: invoice.pdf (125.50 KB)
   Attached to: Sales Invoice/INV-2024-001
   Local path: /private/files/invoice.pdf
   ✅ Uploaded to: frappe-files/2024/10/abc123_invoice.pdf

📄 def456: employee_photo.jpg (89.25 KB)
   Attached to: Employee/EMP-00001
   Local path: /public/files/employee_photo.jpg
   ✅ Uploaded to: frappe-files/2024/10/def456_employee_photo.jpg

⚠️  SKIP: ghi789 - Already in cloud storage (s3_key: frappe-files/old/file.pdf)

================================================================================
MIGRATION SUMMARY
================================================================================
Total files found:     250
Successfully migrated: 248
Skipped:               2
Failed:                0
Total size:            45.67 MB

✅ Migration completed!
================================================================================
```

### Error Handling

Failed migrations are logged to Error Log. Common issues:

- **Local file not found:** File record exists but physical file is missing
- **Already migrated:** File has `s3_key` value (skipped automatically)
- **Upload failure:** Network issues or insufficient permissions
- **Storage quota:** Exceeds cloud storage limits

## Migrating to New Path Strategy

The `migrate-cloud-storage-paths` command updates existing cloud storage files from legacy paths to the new path strategy.

### Basic Usage

```bash
bench --site mysite migrate-cloud-storage-paths
```

### Options

| Option | Type | Description |
|--------|------|-------------|
| `--dry-run` | flag | Preview migration without moving files |
| `--limit` | integer | Maximum number of files to migrate |
| `--batch-size` | integer | Number of files to process per batch (default: 100) |

### Examples

**Preview path migration:**

```bash
bench --site mysite migrate-cloud-storage-paths --dry-run
```

**Migrate specific number of files:**

```bash
bench --site mysite migrate-cloud-storage-paths --limit 100
```

**Migrate with custom batch size:**

```bash
bench --site mysite migrate-cloud-storage-paths --batch-size 50
```

### Migration Process

The migration script:

1. Checks that `use_legacy_paths` is disabled in configuration
2. Finds files with legacy path format (multiple slashes)
3. For each file:
   - Generates new path using current strategy
   - Checks if destination already exists
   - Copies file to new location in cloud storage
   - Updates File document with new `s3_key`
   - Deletes old file from cloud storage
4. Commits changes in batches
5. Shows summary statistics

### Output Example

```
================================================================================
Cloud Storage Path Migration
================================================================================
Bucket:       my-bucket
Folder:       frappe-files
================================================================================

Found 150 files with legacy paths

📄 abc123: invoice.pdf
   Attached to: Sales Invoice/INV-2024-001
   Old path: Sales Invoice/INV-2024-001/invoice.pdf
   New path: frappe-files/abc123_invoice.pdf
   ✅ Migrated

📄 def456: report.xlsx
   Attached to: No attachment
   Old path: File/def456/report.xlsx
   New path: frappe-files/def456_report.xlsx
   ✅ Migrated

⚠️  SKIP: ghi789 - Path unchanged

================================================================================
MIGRATION SUMMARY
================================================================================
Total files found:     150
Successfully migrated: 148
Skipped:               2
Failed:                0

✅ Path migration completed!
================================================================================
```

### Error Handling

Path migration errors are logged to Error Log. Common issues:

- **Source not found:** File record exists but file missing in cloud storage
- **Destination exists:** New path already has a file (skipped, record updated)
- **Copy failure:** Insufficient permissions or network issues
- **Legacy paths enabled:** Must disable `use_legacy_paths` first
