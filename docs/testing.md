# Testing strategy

1. Run the local pytest suite on supported Python versions. Temporary Git books and SQLite databases cover command-line behavior, book traversal and edition filtering, configuration, stored metadata, ordering, malformed input, rollback, invalid dependencies, and cycles.
2. Test integration against a separately checked-out official LFS tag. Set `KLINIX_TEST_LFS_SOURCE` and `KLINIX_TEST_LFS_REVISION` for the real-book integration test. Check paths, artifacts, package recipes, and phase order against the book.
3. To exercise reviewed execution, set `KLINIX_TEST_BUILD_SOURCES` to the local LFS 13.1 Bzip2 archive and patch directory along with `KLINIX_TEST_LFS_SOURCE` and `KLINIX_TEST_LFS_REVISION` for LFS 13.1. Run `python -m pytest` in an unprivileged Bubblewrap-capable shell. Build tests verify the staged package, manifest, checksum failures, read-only inputs, and repeat-build target protection.
4. Run privileged tests, filesystem image tests, chroot tests, and QEMU tests only with explicit opt-in and isolated targets.

Set `KLINIX_TEST_BOOTSTRAP_SOURCES` to the directory of pinned cross-toolchain archives to enable the real Binutils test. Also set `KLINIX_TEST_GCC=1` to run the longer Binutils-to-GCC integration test. Both use `KLINIX_TEST_LFS_SOURCE` and `KLINIX_TEST_LFS_REVISION` for LFS 13.1 and require a working rootless Bubblewrap environment.

Set `KLINIX_TEST_SYSROOT_SOURCES` to a directory containing all the archives and patches from the `chapter05/glibc` build preview to run the four-stage Glibc integration test. The test verifies the staged Linux headers, ELF interpreter, and actual libc used at runtime.

Set `KLINIX_TEST_SHELL_SOURCES` to the complete `chapter06/bash` material set to test the bootstrap through the temporary shell. This also verifies target libstdc++, M4 macro expansion, and the staged Ncurses programs.

Set `KLINIX_TEST_CHAPTER6_SOURCES` to the complete `chapter06/binutils-pass2` material set to test the bootstrap through the target-native assembler and linker. Package probes run against the staged libraries. The test checks File's magic database, the Coreutils directory layout, and the final ELF produced by Binutils.

Set `KLINIX_TEST_GCC_PASS2_SOURCES` to the complete `chapter06/gcc-pass2` material set to run both compiler passes and verify the native C and C++ compilers in the staged root. The test uses unprivileged Bubblewrap namespaces and checks that the target verification leaves the published mount points empty.
