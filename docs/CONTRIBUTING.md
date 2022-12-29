# Cloud Storage Developer Setup

First, set up a new bench and substitute a path to the python version to use. Python should be 3.8 latest for V13 and 3.10 latest for V14. These instructions use [pyenv](https://github.com/pyenv/pyenv) for managing environments.

```shell
# Version 13
bench init --frappe-branch version-13 {{ bench name }} --python ~/.pyenv/versions/3.8.12/bin/python3

# Version 14
bench init --frappe-branch version-14 {{ bench name }} --python ~/.pyenv/versions/3.10.3/bin/python3
```

Create a new site in that bench
```shell
cd {{ bench name }}
bench new-site {{ site name }} --force --db-name {{ site name }}
```

Download the ERPNext app
```shell
# Version 13
bench get-app erpnext --branch version-13

# Version 14
bench get-app erpnext --branch version-14
```

Download the Cloud Storage application
```shell
bench get-app cloud_storage git@github.com:agritheory/cloud_storage.git
```

Install the apps to your site
```shell
bench --site {{site name}} install-app erpnext cloud_storage

# Optional: Check that all apps installed on your site
bench --site {{ site name }} list-apps
```

Set developer mode in `site_config.json`
```shell
nano sites/{{ site name }}/site_config.json
# Add this line:
  "developer_mode": 1,
```

Add the site to your computer's hosts file to be able to access it via: `http://{{ site name }}:[8000]`. You'll need to enter your root password to allow your command line application to make edits to this file.
```shell
bench --site {{site name}} add-to-hosts
```

Set the following keys in your site's configuration file:

```jsonc
{
  ...
  "cloud_storage_settings": {
    "region": "",
    "endpoint_url": "",
    "access_key": "",
    "secret": "",
    "bucket": "",
    "folder": "",
    "expiration": "",
  }
  ...
}
```

Launch your bench
```shell
bench start
```
