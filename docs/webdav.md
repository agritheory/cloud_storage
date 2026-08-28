<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# Desktop File Access via WebDAV

<div class="byline">
  lauty95 2026-06-03
</div>


Cloud Storage exposes the Frappe File list as a WebDAV endpoint at `/dav/`.
Users can connect to it from desktop clients such as macOS Finder, Windows with
rclone, or any compatible WebDAV client.

Files remain stored in the configured cloud storage provider. Frappe remains the
source of truth for file metadata, folders, and permissions.

## Before You Begin

Make sure Cloud Storage is installed and configured for your site. See
[Cloud Storage Configuration](configuration.md) for provider setup.

Each user connects with their own Frappe API key and API secret. To generate
credentials:

1. Click your avatar in the top right of the Frappe interface.
2. Open **My Settings**.
3. Scroll to **API Access** and click **Generate Keys**.
4. Copy the **API Key** and **API Secret**. The secret is shown only once.

## WebDAV URL

Use your site URL with `/dav/` appended:

```text
https://your-site.example.com/dav/
```

For a local development site, use the bench port for your site:

```text
http://your-site.localhost:8000/dav/
```

## macOS Finder

1. Open **Finder**.
2. Select **Go > Connect to Server...**.
3. Enter your WebDAV URL.
4. When prompted, use your Frappe API key as the username and your API secret as the password.

## Windows

Windows users can mount the WebDAV endpoint with rclone.

1. Download and install rclone from [rclone.org/downloads](https://rclone.org/downloads/).
2. Download and install WinFsp from [winfsp.dev/rel](https://winfsp.dev/rel/).
3. Configure a WebDAV remote with `rclone config`.
4. Mount the remote with `rclone mount`.

## rclone

Create a WebDAV remote with rclone's interactive setup:

```shell
rclone config
```

Suggested setup:

```shell
n) New remote
name> cloud_storage
Storage> webdav
url> https://your-site.example.com/dav/
vendor> other
user> your-api-key
pass> your-api-secret
```

Use your Frappe API key as the username and your API secret as the password.
rclone stores the password in its configuration using its own obscured format.

The resulting configuration will look similar to this:

```ini
[cloud_storage]
type = webdav
url = https://your-site.example.com/dav/
vendor = other
user = your-api-key
pass = obscured-api-secret
```

Example commands:

```shell
rclone ls cloud_storage:
rclone copy ./report.pdf cloud_storage:Reports/report.pdf
rclone copy cloud_storage:Reports/report.pdf ./report.pdf
```

On Windows, mount the remote as a drive letter:

```shell
rclone mount cloud_storage: X:
```

## Permissions

The WebDAV mount follows Frappe File permissions. Users can only list, read,
create, move, or delete files when their Frappe permissions allow the same
operation.

Files created through WebDAV are private by default.

## Unsupported Operations

Cloud Storage does not support WebDAV `COPY` or `PROPPATCH`. Clients should use
regular upload, download, move, and delete operations instead.
