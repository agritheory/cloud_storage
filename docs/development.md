# Cloud Storage Developer Setup

Before you begin, make sure that your Python version is:
- Latest 3.10 for Frappe's version 14.

1. First, set up a new bench and substitute a path to the python version to use. These instructions use [pyenv](https://github.com/pyenv/pyenv) for managing environments.

```shell
# Version 14
bench init --frappe-branch version-14 {{ bench name }} --python ~/.pyenv/versions/3.10.3/bin/python3
```

2. Create a new site in that bench
```shell
cd {{ bench name }}
bench new-site {{ site name }} --force --db-name {{ site name }}
```

3. Download the Cloud Storage application
```shell
bench get-app cloud_storage https://github.com/agritheory/cloud_storage.git
```

4. Install the app to your site
```shell
bench --site {{site name}} install-app cloud_storage

# Optional: Check that the app installed on your site
bench --site {{ site name }} list-apps
```

5. Set developer mode in `site_config.json`
```shell
nano sites/{{ site name }}/site_config.json
# Add this line:
  "developer_mode": 1,
```

6. Add the site to your computer's hosts file to be able to access it via: `http://{{ site name }}:[8000]`. You'll need to enter your root password to allow your command line application to make edits to this file.
```shell
bench --site {{site name}} add-to-hosts
```

7. Make sure to configure your S3 instance to access your files. You can do this by setting the permissions defined in the [configuration guide](configuration.md).

8. Install the libmagic C library. The [documentation for the python-magic package](https://pypi.org/project/python-magic/) (a Cloud Storage dependency) notes that the package is a wrapper around the libmagic C library, which needs to be installed on the system as well.
```shell
# Debian/Ubuntu
sudo apt-get install libmagic1

# Windows
pip install python-magic-bin

# OSX Homebrew
brew install libmagic

# OSX macports
port install file
```

9. Once everything is set up, launch your bench.
```shell
bench start
```

10. To run `mypy` locally:
```shell
source env/bin/activate
mypy ./apps/cloud_storage/cloud_storage --ignore-missing-imports
```