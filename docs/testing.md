# Testing strategy

1. Run the local pytest suite on supported Python versions. Temporary Git books and SQLite databases cover command-line behavior, book traversal and edition filtering, configuration, stored metadata, ordering, malformed input, rollback, invalid dependencies, and cycles.
2. Test integration against a separately checked-out official LFS tag. Set `KLINIX_TEST_LFS_SOURCE` and `KLINIX_TEST_LFS_REVISION` for the real-book integration test. Check paths, artifacts, package recipes, and phase order against the book.
3. To exercise reviewed execution, set `KLINIX_TEST_BUILD_SOURCES` to the local LFS 13.1 Bzip2 archive and patch directory along with `KLINIX_TEST_LFS_SOURCE` and `KLINIX_TEST_LFS_REVISION` for LFS 13.1. Run `python -m pytest` in an unprivileged Bubblewrap-capable shell. Build tests verify the staged package, manifest, checksum failures, read-only inputs, and repeat-build target protection.
4. Run privileged tests, filesystem image tests, chroot tests, and QEMU tests only with explicit opt-in and isolated targets.

Set `KLINIX_TEST_BOOTSTRAP_SOURCES` to the directory of pinned cross-toolchain archives to enable the real Binutils test. Also set `KLINIX_TEST_GCC=1` to run the longer Binutils-to-GCC integration test. Both use `KLINIX_TEST_LFS_SOURCE` and `KLINIX_TEST_LFS_REVISION` for LFS 13.1 and require a working rootless Bubblewrap environment.
