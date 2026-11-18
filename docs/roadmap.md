# Development roadmap

| Milestone | Status and remaining work |
| --- | --- |
| 1. Foundation | Python package, CLI, logging, configuration, documentation, and automated tests |
| 2. LFS import | Official LFS Git discovery, pinned local DocBook import, systemd filtering, material metadata, and offline checksum verification. Verified against LFS 12.4, 13.1, and a development revision. |
| 3. Catalog and planner | Transactional SQLite catalog, typed dependency graph, and dry-run plans. Installed package and build state remain pending. |
| 4. Build engine | Implemented host preflight, isolated Bzip2 staging, the LFS 13.1 cross-toolchain through Glibc and libstdc++, and the M4, Ncurses, and Bash temporary stages. Next, build Coreutils and the remaining Chapter 6 tools, then add a target chroot. |
| 5. Recovery and installation | Single-package build logs and manifests are available. Add checkpoints, installed inventory, ownership checks, safe removal, and upgrade/recovery semantics. |
| 6. Disk and boot | Add explicit privileged operations, disk image layouts, mounts, fstab, and bootloader handling |
| 7. Graphical Builder | Add Qt configuration, selections, live plan execution, and recovery views backed by headless APIs |
| 8. Additional books | Validate BLFS, MLFS, GLFS, and SLFS providers and extend package dependencies |
| 9. Hardware, firmware, and kernel | Add profile detection, Kconfig layers, initramfs, firmware, and external module integration |
| 10. Installed package manager | Deploy the headless CLI in the target and implement installed-package operations and an optional GUI |
| 11. Validation and release | Add optional QEMU boot checks, isolated end-to-end tests, packaging, and user documentation |

The book-wide dry-run plan covers source and patch metadata and selected DocBook build stages. Real execution is limited to the [reviewed directory builds](directory-build.md). Full book execution and bootloader configuration remain pending.
