---
# These are optional elements. Feel free to remove any of them.
status: proposed
date: 2023-02-03
deciders: tyler@agritheory.com
---
# Check Permissions Before Serving Private Files

## Context and Problem Statement

For files that are 'private' these are theoretically accessible by any user inside the system, even if this isn't appropriate.
Cloud Storage provides two opinions about this:
 - If the user has permissions for the document the file is attached to, there's no change in behavior
 - If the user does not have permissions for the document the file is attached to, they should be denied access

This should be checked on every request against the file. 

## Decision Outcome
Appropriate of access to attachments. If there are differing opinions about this, it can be overridden by subclassing `CustomFile` and providing a custom `has_permission` function.

