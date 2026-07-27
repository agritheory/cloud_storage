<!-- Copyright (c) 2025, AgriTheory and contributors
For license information, please see license.txt-->

## Cloud Storage

Frappe App for integrating with cloud storage applications

### Installation Instructions

See the installation guides for detailed instructions for either a [production](docs/production.md) or [development](docs/development.md) environment.

### Testing

```shell
source env/bin/activate
bench setup requirements --dev
bench --site {{ site name }} install-app cloud_storage
bench --site {{ site name }} execute 'cloud_storage.tests.setup.before_test'

cd apps/cloud_storage
pytest cloud_storage/tests/ -v
```

`before_test` runs ERPNext setup wizard data, cloud_storage prerequisites, and shared test users. Run it after install or site reinstall.

#### License

MIT
