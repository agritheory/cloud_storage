---
# These are optional elements. Feel free to remove any of them.
status: proposed
date: 2023-02-03
deciders: tyler@agritheory.com
---
# No Expiration on Public Files

## Context and Problem Statement

For files that are 'public' and can be served to anyone with the URL, don't provide a time expiry value.
Permissions are also bypassed for these files. 

## Decision Outcome

This will improve user experience with files that are meant to be linked or widely accessible.

### Consequences

* Good, because if the file is public, it should be accessible by any user agent at any time.
* Bad, because it still requires a call to the 'retrieve' API. It would be better to provide a direct link to the S3 block that is hosting the file, but buckets are either public (CDN-ish) or private. 

