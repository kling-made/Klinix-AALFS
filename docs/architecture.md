# Software architecture

The CLI calls the headless Python backend. The future Builder and installed GUI will use the same interfaces without importing CLI presentation code.

| Component | Responsibility |
| --- | --- |
| `books` | Provider interface, release discovery, immutable Git input, and LFS-specific DocBook parsing |
| `recipes` | Validated value objects for pinned provenance, materials, recipes, and dependencies |
| `catalog` | Transactional SQLite persistence and schema versioning |
| `dependencies` | Deterministic topological ordering, policy application, and graph validation |
| `planner` | Package/recipe selection and dry-run assembly for one pinned book |
| `sources` | Match local source archives and patches against checksums from the pinned book |
| `builder` | Pin executable adapters, run the isolated host toolchain, and publish staged directory output |
| `config` | Local paths and build policy consumed by entry points |
| `cli` | Argument parsing, output, and user-facing errors |

The LFS provider reads DocBook from Git objects. It follows selected chapters in book order, extracts package metadata and display-only command blocks, and connects consecutive sections with ordering edges. LFS Appendix C describes dependencies across multiple bootstrap environments, sometimes with cycles. The initial provider uses the explicit book sequence to model bootstrap ordering. The graph supports classified edges for future reviewed package dependencies.

The Bzip2 adapter stages a package through a fixed host-toolchain sandbox. The bootstrap adapters also mount a private sysroot at `/mnt/lfs`, where GCC pass 1 can use the Binutils installed by the preceding stage. Later temporary-tool and chroot stages will need controlled target execution contexts. Installation ownership and recovery will share a transaction boundary. GUI workers will consume progress events produced by the backend.

See [recipe format](recipes.md), [database schema](database.md), and [security model](security.md).
