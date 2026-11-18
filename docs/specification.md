# Project specification

Klinix is an independent GPL-3.0-or-later project. Its Builder will automate creating bootable x86-64 systemd Linux systems with a PySide6 interface. Its installed package manager will provide a headless CLI and an optional GUI. Both applications will share book providers, a recipe model, dependency resolution, build orchestration, and a SQLite database inside the installed system.

## Initial release

An installable Python package must provide a working `klinix` command. It must import a local systemd LFS Git source at an exact revision, record package inputs and inspectable recipes in SQLite, and display a deterministic dry-run build plan. The dependency engine must reject missing references and cycles and support separate required, recommended, and optional policies. An official pinned book and local fixtures must exercise the importer.

## Product requirements

- Support standard LFS and MLFS as compatible base variants and BLFS, GLFS, and SLFS as add-on families. Discover official stable and development revisions, pin sources, record checksums and signatures where available, and make compatibility explicit.
- Offer directory, disk image, and explicitly approved physical targets, plus reopening an existing installation. Support filesystem, swap, mount, bootloader, account, locale, networking, and compilation settings. Keep credentials outside persisted configuration.
- Run builds through independent stages for host validation, source acquisition, toolchain bootstrap, temporary tools, chroot, final packages, system configuration, kernel, bootloader, and verification. Record progress, logs, interruptions, safe retries, and partial operations.
- Track manifests, ownership, modified configuration files, package history, dependencies, and ABI-sensitive rebuilds. Support search, install, upgrade, rebuild, verify, and removal from the installed CLI.
- Offer host-optimized, portable, and custom hardware profiles. Merge Kconfig defaults, detected requirements, boot features, package requirements, and overrides. Track firmware and microcode, as well as book-supported proprietary drivers and their external kernel modules.
- Present real configuration, package selections, build plans, build logs, recovery, and inventory in the GUI. Run lengthy work outside the UI thread. Keep QEMU validation optional and record its results independently of build success.
