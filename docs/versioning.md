
Versioning must be enabled at the bucket level.

To enable file versioning, enter the following in bench console:
```ipython
In [1]: from cloud_storage.cloud_storage.overrides.file import get_cloud_storage_client

In [2]: client = get_cloud_storage_client()

In [3]: client.put_bucket_versioning(Bucket=client.bucket, VersioningConfiguration={'Status': 'Enabled'})
```
