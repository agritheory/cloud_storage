# Cloud Storage Site Configuration

The following documentation shows how to set up some common cloud storage providers:

- [Amazon Web Services S3](aws-s3.md)
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
  }
  ...
}
```
