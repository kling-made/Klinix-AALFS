# Changelog

## 0.7.0

- Added the pinned LFS 13.1 GCC pass 2 build, native `cc`, and the target C and C++ compiler and runtime libraries.
- Added read-only target-root verification with Bubblewrap. The staged compiler builds, links, and runs C and C++ probes that exercise the target loader, POSIX threads, and C++ exceptions.

## 0.6.0

- Added reviewed Chapter 6 builds for Coreutils, Diffutils, File, Findutils, Gawk, Grep, Gzip, Make, Patch, Sed, Tar, Xz, and Binutils pass 2.
- Generate File's magic database with a native helper, stage Coreutils' `chroot` under `/usr/sbin`, and verify the target-native assembler and linker with a small ELF.
- Exercise each temporary program with the staged loader before publishing the sysroot.

## 0.5.0

- Added pinned builds for target libstdc++, GNU M4, Ncurses, and Bash, including the target `/bin/sh` link.
- Check that a C++ program loads the staged libstdc++, M4 expands a macro, Ncurses reads the staged terminal database, and Bash runs in POSIX mode.
- Verify and stage the GCC source once for the compiler and target C++ library stages.

## 0.4.0

- Added the Linux API headers and cross-Glibc stages for the pinned LFS 13.1 sysroot.
- Build transitive prerequisites in order and validate the staged loader, startup files, and libc with a dynamically linked program before publishing.
- Preserve contained source symlinks during extraction and record the merged library layout in the build manifest.

## 0.3.0

- Added reviewed LFS 13.1 Binutils and GCC pass 1 adapters with an isolated sysroot and bootstrap host checks.
- Added ordered prerequisite execution, XZ source archives, and hardlink tracking for groups contained in the staged target.
- Added assembler/linker and freestanding C and C++ checks before publishing a toolchain root.

## 0.2.0

- Added an isolated host preflight and executable dry-run preview for the reviewed LFS 13.1 Bzip2 adapter.
- Added rootless compilation, upstream sample tests, and staging into a new directory with a file and symlink manifest.
- Added reviewed SHA-256 source pins, constrained archive extraction, atomic target publication, and persistent build logs.

## 0.1.0

- Added the headless Klinix CLI, local configuration, and development specifications.
- Added official LFS Git discovery and pinned local systemd DocBook import with package, source, patch, and reference command metadata.
- Added the SQLite recipe catalog and deterministic dry-run planning with dependency policy, missing-reference errors, and cycle detection.
- Added offline verification of local archives and patches against published checksums.
