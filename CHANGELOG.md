# CHANGELOG



## v14.4.0 (2024-02-07)

### Feature

* feat: move FileUploader to Vue 3 (#50) ([`4c528c8`](https://github.com/agritheory/cloud_storage/commit/4c528c85922ba11a998390654c55ddac1bb1d621))

### Fix

* fix: handle URL formats with hashtag characters (v15) (#55) ([`41d55a8`](https://github.com/agritheory/cloud_storage/commit/41d55a865f49ad57aa384b17bdec54ce41d42b8a))


## v14.3.0 (2024-01-19)

### Ci

* ci: migrate to Python semantic release (#40)

* ci: migrate to Python semantic release

* ci: change db logger

* chore: add type ignore for File doctype

---------

Co-authored-by: Tyler Matteson &lt;tyler@agritheory.com&gt; ([`38398f0`](https://github.com/agritheory/cloud_storage/commit/38398f0031bbb93031828bc5cd46db508b9f6dc2))

### Feature

* feat: versioned files (#36)

* wip: versioning doctype

* wip: file versioning

* fix: remove print

* wip: override upload dialog

* feat: run name and content validations on adding file

* feat: setup rename for name-conflicts

* feat: show existing filenames for content hash conflicts

* fix: error text on filename conflict

* feat: override existing file instead of creating a new one

* ci: update pre-commit config

* test: fix write_file test

* feat: add file association if content hash matches

* fix: catch errors while stripping EXIF data from file

* ci: update json-diff dependency

* fix: allow error message translations

* test: fix write_file test

* fix: set values in database if a record exists already

* test: fix write_file test

* test: disable db logger in test

* fix: avoid file size checks since that data may not exist

* fix: allow skipping content hash conflicts

* Revert &#34;fix: allow skipping content hash conflicts&#34;

This reverts commit e731eb0c6ffa7bcfb3f78d3cf8032c0b5c40a450.

* fix: optimize content validation logic

* fix: check file permission with full object

* fix: file permission for orphaned files

* style: ignore type errors

* fix: handle file attachments from existing library

* fix: version table indexes when associations are removed

* fix: add metadata to version and file association tables

* fix: allow keeping file name structure

* fix: index numbering issues for version table

---------

Co-authored-by: Rohan Bansal &lt;rohan@parsimony.com&gt; ([`331c1ef`](https://github.com/agritheory/cloud_storage/commit/331c1ef19fadeda63b66768725ced4609ba96bde))

### Fix

* fix: update dependencies for version 15 (#49) ([`a467f3f`](https://github.com/agritheory/cloud_storage/commit/a467f3fa78726adb59ec84477282cae7bbee9be5))

* fix: run file validation on drag-n-drop and capture events (#43) ([`605aae6`](https://github.com/agritheory/cloud_storage/commit/605aae6e7a2a69300e507015fddd0b6cd61221f4))

* fix: allow whitespace in file name (#42) ([`9d16ffa`](https://github.com/agritheory/cloud_storage/commit/9d16ffa631b56b9872da5d89091ec3ef5cd73629))


## v14.2.3 (2023-08-02)

### Fix

* fix: set correct data type for permission check (#39) ([`114d579`](https://github.com/agritheory/cloud_storage/commit/114d579663ef4e8d1a76e01be3b395596c665511))


## v14.2.2 (2023-07-24)

### Fix

* fix: explicitly use s3v4 protocol for compatibility with backblaze (#29) ([`c813f69`](https://github.com/agritheory/cloud_storage/commit/c813f69df93fac59f72af66311638ce3a4ea7d63))


## v14.2.1 (2023-05-17)

### Fix

* fix: recursion bug while adding file associations (#33)

* fix: recursion bug while adding file associations

* test: add test for file associations ([`dd7ee93`](https://github.com/agritheory/cloud_storage/commit/dd7ee932fb4c6b18e4880791d4c016b2ee3caa87))


## v14.2.0 (2023-05-04)

### Feature

* feat: get remote file content before sending email (#32) ([`d4e05e7`](https://github.com/agritheory/cloud_storage/commit/d4e05e798542a499463bbfcf07f2bd047080d98f))


## v14.1.3 (2023-05-02)

### Fix

* fix: check if the app is installed before monkey-patch ([`7f5b036`](https://github.com/agritheory/cloud_storage/commit/7f5b036bf4d831a8aad20d8ec9eb58b4b01174fe))


## v14.1.2 (2023-04-11)

### Fix

* fix: ignore user permissions when renaming file ([`ce5874f`](https://github.com/agritheory/cloud_storage/commit/ce5874fe15eac7da8d45d37114b4a859edc84e7d))


## v14.1.1 (2023-04-04)

### Unknown

* Merge pull request #27 from agritheory/ci

Ci ([`4dd5441`](https://github.com/agritheory/cloud_storage/commit/4dd5441bf6c5c5522dbae63c957ae4382ee50c8e))

* Merge branch &#39;version-14&#39; into ci ([`49b61c6`](https://github.com/agritheory/cloud_storage/commit/49b61c6d4f48c38a3310ff615a84237d0068cbbc))


## v14.1.0 (2023-04-04)

### Chore

* chore: get ci working with semantic release ([`8c5354b`](https://github.com/agritheory/cloud_storage/commit/8c5354b4351d6e825da0088f4b40a6a39cbd225e))

### Documentation

* docs: fix routing in index.md ([`f6550e6`](https://github.com/agritheory/cloud_storage/commit/f6550e606efb77eeeb62db54497813472fd6d44a))

* docs: add index page, screen shots, sharing links info ([`04e9a3f`](https://github.com/agritheory/cloud_storage/commit/04e9a3fd64080c0af179c968716a9114a2373af3))

* docs: add configuration docs for backblaze ([`d6fefbc`](https://github.com/agritheory/cloud_storage/commit/d6fefbcda90260f9c81e8157a0384f53fc714b81))

* docs: add documentation for setting up the app (#9)

* docs: add documentation for setting up the app

* docs: grammar edits

* docs: remove ERPNext from installation guides

Co-authored-by: Heather Kusmierz &lt;heather.kusmierz@gmail.com&gt; ([`05812a4`](https://github.com/agritheory/cloud_storage/commit/05812a4240c3415601ccbe5e2e7c062987221c43))

### Feature

* feat: add method for preservinvg customizations ([`88c94c6`](https://github.com/agritheory/cloud_storage/commit/88c94c6e7dd56a524e5358366b116f60d355983b))

* feat: shorten public urls, add public persistent sharing link API ([`139e370`](https://github.com/agritheory/cloud_storage/commit/139e3703887ef6b0cacc15d961e8065dedf38288))

* feat: allow attachments with Amazon S3 (#1)

* feat: allow attachments with Amazon S3

* docs: add typings and documentation

* fix: add client validations

* test: add test for uploading files

* test: add test for processing files for upload

* test: add test for deleting files

* ci: default ci branch to v14

* ci: add linter and release action

* style: prettify code ([`a44ad57`](https://github.com/agritheory/cloud_storage/commit/a44ad572b097c178863b36482f82d02174173ccb))

* feat: initial commit ([`4fa4d56`](https://github.com/agritheory/cloud_storage/commit/4fa4d567b2569b2099c0932a2074e9f97426159a))

* feat: Initialize App ([`cd3b50f`](https://github.com/agritheory/cloud_storage/commit/cd3b50f262be1596ff65ad5b7192480c4e977a51))

### Fix

* fix: release ([`f112fe7`](https://github.com/agritheory/cloud_storage/commit/f112fe7a6357ad0d0ea2355c2523be1931d4c577))

* fix: release CI (#26) ([`258e1c2`](https://github.com/agritheory/cloud_storage/commit/258e1c217e308aaf503c957d1c3b6ef3f690c5a9))

* fix: release CI ([`2ce184e`](https://github.com/agritheory/cloud_storage/commit/2ce184e555a95cebc91d45de35280b6ade8a28f8))

* fix: allow selection from library ([`aba4445`](https://github.com/agritheory/cloud_storage/commit/aba44451f55c6c5ae8f057b9a568f6cbfb7e681e))

* fix: user permission check ([`82cdf4d`](https://github.com/agritheory/cloud_storage/commit/82cdf4d35252874ca942f9eac716568278dfd1c4))

* fix: fsjd linter ([`c1e67b9`](https://github.com/agritheory/cloud_storage/commit/c1e67b9b3f716bea59403fa0cb9d4ac90e58b9cc))

* fix: code cleanup ([`904dba1`](https://github.com/agritheory/cloud_storage/commit/904dba1226ed68d165ba7a5eecdff541f53700d9))

* fix: mypy / return statements that don&#39;t actually return anything ([`218cb8b`](https://github.com/agritheory/cloud_storage/commit/218cb8b95508e417379a3c54ea87deccfc8019c4))

* fix: error in markdown/yml format (#10) ([`14e1b01`](https://github.com/agritheory/cloud_storage/commit/14e1b0122033b74dde66ea277d7765374f53ae96))

* fix: tab size editor config (#3) ([`46e6e00`](https://github.com/agritheory/cloud_storage/commit/46e6e005951c8a01faa15d6972c2e0f120fb23bd))

### Style

* style: prettify code ([`f72ade2`](https://github.com/agritheory/cloud_storage/commit/f72ade2edff2135f6a05709ad2fcf21dbc900d2a))

### Test

* test: file permissions ([`2e709f0`](https://github.com/agritheory/cloud_storage/commit/2e709f053b5433204cc5757f4270ddc614cdcb2c))

### Unknown

* File association (#23)

* fix: allow selection from library

* wip: file association

 -[x] monkey patch `get_attachments`
 -[x] add schema / child table
 -[ ] add validation for file association
 -[ ] rollback if associating to an existing file instead of creating a new record

* feat: remove association with file instead of deleting unless it&#39;s the only one

* feat: remove associations correctly, make storage details perm level 1

* chore: black and mypy

* cust: allow preview to be hidden, change column names

* docs: add multiple file association explanation

* docs: address review (comments and typing)

* wip: refactor to pytest ([`fbdb975`](https://github.com/agritheory/cloud_storage/commit/fbdb975a322f5529384f7ebdb1179188806d9938))

* wip: stub permissions test

tThis isn&#39;t going to work as-is since it&#39;s checking a PO ([`310d627`](https://github.com/agritheory/cloud_storage/commit/310d627aea25d649cfa45dbc3924d68bcffd291e))

* docs/test: fix tests, add documentation ([`05a0e78`](https://github.com/agritheory/cloud_storage/commit/05a0e78058d6f26a751593b51310bbe483e14c64))

* feat/docs: appropriate time expiry and permissions checks ([`e0932f2`](https://github.com/agritheory/cloud_storage/commit/e0932f2bf1413aed9021a6251e4b5b6fe271dfde))

* Release fix (#6)

* fix: tab size editor config

* fix: add release to package.json

* fix: try releaserc.json instead

* fix: add name to package.json for release ([`cbdf818`](https://github.com/agritheory/cloud_storage/commit/cbdf81854b75951244f3533dd56d9e377b8f6792))

* Release fix (#5)

* fix: tab size editor config

* fix: add release to package.json

* fix: try releaserc.json instead ([`a5536d5`](https://github.com/agritheory/cloud_storage/commit/a5536d5ec79e861e43a21607015e90091c3345a7))

* Release fix (#4)

* fix: tab size editor config

* fix: add release to package.json ([`fff7566`](https://github.com/agritheory/cloud_storage/commit/fff756620527ca1abf824c4214ce7c5abdf825ce))
