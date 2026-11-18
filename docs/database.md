# Package database

The SQLite catalog uses `PRAGMA application_id` to identify Klinix databases and `PRAGMA user_version` for schema revisions. Schema 1 records imported metadata.

| Table | Contents |
| --- | --- |
| `book` | Family, edition, version, exact revision, instruction license, and import fingerprint |
| `package` | Book-scoped product name and version |
| `artifact` | Book-scoped source or patch URL and published digest |
| `recipe` | Book-scoped ID, chapter, sequence, source path, and optional package |
| `recipe_artifact` | Recipe references to primary and detected supplementary materials |
| `step` | Ordered reference text, book role and remap, and whether manual review is indicated |
| `dependency` | Recipe-to-recipe edges within the same book, with a policy classification |

Foreign keys constrain references to the same book. Imports are atomic. Reimporting an identical snapshot is idempotent, and a content mismatch for an existing pin produces an error. A future importer that changes stored recipe semantics will need an explicit migration or recipe-format revision.

Later migrations will add build sessions with immutable plan hashes, per-step checkpoints, installed packages and variants, file ownership, manifests, configuration-file state, and upgrade history. Catalog presence establishes book availability. Installation state will come from its own transactional inventory.
