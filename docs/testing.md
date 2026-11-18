# Testing strategy

1. Run the local pytest suite on supported Python versions. Temporary Git books and SQLite databases cover command-line behavior, book traversal and edition filtering, configuration, stored metadata, ordering, malformed input, rollback, invalid dependencies, and cycles.
2. Test integration against a separately checked-out official LFS tag. Set `KLINIX_TEST_LFS_SOURCE` and `KLINIX_TEST_LFS_REVISION` for the real-book integration test. Check paths, artifacts, package recipes, and phase order against the book.
3. When execution is added, exercise isolated tiny build inputs before attempting a full book. Cover interruption, recovery, filesystem escapes, ownership conflicts, upgrades and removal, kernel config merging, and firmware selection.
4. Run privileged tests, filesystem image tests, chroot tests, and QEMU tests only with explicit opt-in and isolated targets.
