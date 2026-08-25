<!-- Copyright (c) 2025, AgriTheory and contributors
For license information, please see license.txt-->

# Cloud Storage Site Configuration

<div class="byline">
  Rohan Bansal, Ishwarya, Heather Kusmierz, lauty95, Tyler Matteson, and Francisco Roldán 2026-08-25
</div>


The following documentation shows how to set up some common cloud storage providers:

- [Amazon Web Services S3](aws-s3.md)
- [Backblaze B2](backblaze-b2.md)
- [DigitalOcean Spaces](digitalocean-spaces.md)

## App Configuration

Set the following keys in your site's configuration file (`/sites/{site_name}/site_config.json`):

```json
{
  ...
  "cloud_storage_settings": {
    // the ID of the region where your bucket is located
    "region": "s3-region-name",

    // the endpoint URL for your S3 instance
    "endpoint_url": "s3-endpoint-url",

    // S3 access key ID
    "access_key": "s3-access-key",

    // S3 secret access key
    "secret": "s3-secret-key",

    // the name of the S3 bucket
    "bucket": "bucket-name",

    // the name of the folder inside the bucket where the files will be stored
    "folder": "folder-name",

    // (optional) time before the generated URL for the file expires, in seconds
    // default: 120 seconds
    "expiration": 120,

    // Optional: use new path strategy instead of legacy
    // Default: true (uses legacy folder/doctype/attached_to_name/docname paths)
    "use_legacy_paths": 0,

    // Optional: enable the local write-through cache, so this node can keep
    // serving and accepting recently used files during a brief object storage
    // outage. Default: false. Mutually exclusive with "use_local"
    "local_cache_enabled": true,

    // Working-set budget for the local cache, in GB (fractional values are
    // accepted). Default: 50
    "max_cache_size_gb": 50,

    // Hard disk-safety ceiling above max_cache_size_gb. Uploads are rejected
    // once this is exceeded and there is no replicated content left to evict.
    // Default: 80
    "emergency_cache_size_gb": 80,

    // Minutes a recently accessed file is protected from eviction, even when
    // the cache is over max_cache_size_gb. Default: 60
    "cache_retention_minutes": 60,

    // Cache a file locally the first time it's fetched cold from object
    // storage, so a later outage can still serve it. Default: true
    "warm_on_read": true,

    // Stop retrying a file's replication to object storage after this many
    // failed attempts. Default: 10
    "replication_max_retries": 10,

    // Consecutive failed health checks before the circuit breaker flips to
    // Degraded. Default: 3
    "failure_threshold": 3
  }
  ...
}
```
