# Security model

## Current catalog and planner

- Treat local books as external data. Read only relevant regular blobs from a pinned Git commit. Ignore working-tree contents, links, and submodules.
- XML parsing accepts only the book's known entity files and a local character entity table. Disallow arbitrary external entities and path escapes in XML includes. Bound source data and parsed output.
- Capture source URLs and checksums as metadata. Verification reads a local regular file selected by the caller. A Git pin identifies content. Establishing trust requires a separate provenance decision. An MD5 digest published by a book is useful for matching bytes and offers limited tamper resistance.
- The CLI never runs imported recipe text, downloads package sources, mounts filesystems, or modifies storage devices. Dry-run output goes to stdout and catalog imports modify the selected SQLite file.
- Keep source paths, configuration, and logs free of credentials. Use fixed subprocess arguments for Git.

## Execution requirements

Before adding execution, define trusted reviewed recipes, allowlisted privileged operations, and a dedicated privilege boundary. Use unprivileged build identities, controlled environments, safe archive extraction, and file-descriptor-based path handling where needed. Validate target identity and mounts across the whole operation. Require explicit approval with an exact device preview for destructive disk actions.

Record recoverable steps, stage installations before changing ownership records, detect inconsistent state after interruption, and preserve modified configuration files. Validate available signatures against trusted keys and keep logs useful without exposing secrets.
