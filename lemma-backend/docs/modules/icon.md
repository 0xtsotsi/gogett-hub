# Icon module

## Purpose

`app/modules/icon` is a small shared media service for user-uploaded resource
icons. It accepts authenticated uploads, verifies actual bytes are an inert
raster format, stores them in public local/GCS storage, returns a stable URL,
and serves the bytes with MIME-sniffing protection and immutable caching.

## Runtime contributions and API

| Route | Behavior |
| --- | --- |
| `POST /icons/upload` | Read an uploaded image, sniff its magic bytes, store under `icons/{user_id}/{uuid}.{ext}`, return URL/path/type |
| `GET /public/icons/{icon_path}` | Normalize path, read bytes, serve safe raster types inline and legacy/unknown types as attachments |

The module contributes no tables, events, or workers. `IconService.delete_by_url`
lets owner modules clean up a previously managed icon when replacing/deleting a
resource.

## Security boundary

Only PNG, JPEG, GIF, WebP, and BMP magic bytes are accepted. SVG is rejected
because the public route shares a cookie-bearing API origin. Retrieval sets
`X-Content-Type-Options: nosniff`; an unknown/legacy extension is
`application/octet-stream` with `Content-Disposition: attachment`. Storage path
normalization rejects empty, dot, and parent segments.

```mermaid
flowchart LR
    U["Authenticated upload"] --> M["Magic-byte MIME sniff"]
    M -->|safe raster| S["Public storage"]
    M -->|other| X["400"]
    S --> G["Public immutable GET"]
    G --> H["nosniff + safe media type"]
```

## Tests and operations

Tests cover upload validation, public retrieval, traversal rejection, headers,
and cleanup. Current unit coverage is 84.0% (126 of 150 statements), the
highest module. The absence of an upload byte limit is tracked in
[issues.md](issues.md).

