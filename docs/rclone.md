<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# Desktop File Access via WebDAV

Cloud Storage includes a built-in WebDAV server that lets users browse and download their ERPNext files from any WebDAV-compatible desktop client. Files remain stored in S3; ERPNext remains the authoritative registry.

## Starting the Server

```bash
bench webdav-server
```

| Option | Default | Description |
|--------|---------|-------------|
| `--site` | first site | Frappe site to serve |
| `--port` | `8010` | Port to listen on |
| `--host` | `0.0.0.0` | Host/IP to bind to |

## Generating API Credentials

Each user connects with their own Frappe API key and secret:

1. Click your avatar (top right) → **My Settings**
2. Scroll to **API Access** → **Generate Keys**
3. Copy the **API Key** and **API Secret** — the secret is shown only once

## Connecting a Desktop Client

### macOS — Finder

1. **Go → Connect to Server…** (⌘K)
2. Enter `http://yourserver:8010/`
3. Username: **API Key** · Password: **API Secret**

### Windows — Explorer

1. **This PC → Computer → Map network drive**
2. Enter `http://yourserver:8010/`
3. Check **Connect using different credentials**, enter API Key and Secret

### rclone (any OS)

Add to `~/.config/rclone/rclone.conf`:

```ini
[frappe]
type = webdav
url = http://yourserver:8010/
vendor = other
user = <your api_key>
pass = <rclone obscure <your api_secret>>
```

Basic usage:

```bash
rclone ls frappe:
rclone copy "frappe:report.pdf" ~/Downloads/
```

## Production

The built-in server (`wsgiref`) is single-threaded. For production, run through gunicorn + nginx.

**Supervisor** (`/etc/supervisor/conf.d/frappe-webdav.conf`):

```ini
[program:frappe-webdav]
command=/path/to/bench/env/bin/gunicorn \
    --bind 127.0.0.1:8010 \
    --workers 2 \
    --worker-class sync \
    "cloud_storage.cloud_storage.webdav.app:create_webdav_app('mysite.localhost')"
directory=/path/to/bench
user=frappe
autostart=true
autorestart=true
stderr_logfile=/var/log/frappe-webdav.err.log
stdout_logfile=/var/log/frappe-webdav.out.log
```

**nginx** (add to site config):

```nginx
location /dav/ {
    proxy_pass         http://127.0.0.1:8010/;
    proxy_set_header   Host $host;
    proxy_set_header   Authorization $http_authorization;
    proxy_pass_header  Authorization;
    client_max_body_size 100m;
}
```

Update your rclone remote URL to `https://mysite.example.com/dav/`.

## Security

- **Use HTTPS in production.** API credentials are sent as HTTP Basic Auth — always terminate TLS at nginx before exposing outside localhost.
- **Per-user access.** Each user sees only the files they have permission to access in ERPNext.
- Regenerate your API secret in **My Settings → API Access**
