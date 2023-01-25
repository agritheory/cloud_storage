# Cloud Storage Production Setup

Before you begin, make sure that your server's Python version is:
- Latest 3.8 for Frappe's version 13
- Latest 3.10 for Frappe's version 14.

1. First, set up a new bench and substitute a path to the python version to use. These instructions use [pyenv](https://github.com/pyenv/pyenv) for managing environments.

```shell
# Version 13
bench init --frappe-branch version-13 {{ bench name }} --python ~/.pyenv/versions/3.8.12/bin/python3

# Version 14
bench init --frappe-branch version-14 {{ bench name }} --python ~/.pyenv/versions/3.10.3/bin/python3
```

2. Create a new site in that bench
```shell
cd {{ bench name }}
bench new-site {{ site name }} --force --db-name {{ site name }}
```

3. Download the ERPNext app
```shell
# Version 13
bench get-app erpnext --branch version-13

# Version 14
bench get-app erpnext --branch version-14
```

4. Download the Cloud Storage application
```shell
bench get-app cloud_storage git@github.com:agritheory/cloud_storage.git
```

5. Install the apps to your site
```shell
bench --site {{site name}} install-app erpnext cloud_storage

# Optional: Check that all apps installed on your site
bench --site {{ site name }} list-apps
```

6. Make sure to configure your S3 instance to access your files. You can do this by setting the permissions defined in the [configuration guide](configuration.md).

7. Once everything is set up, launch your bench.
```shell
bench start
```
