# Reviewed directory build

The Bzip2 adapter implements the LFS 13.1 systemd Chapter 8 recipe with staging paths. Its exact book revision and reviewed SHA-256 digests are in `src/klinix/builder/approvals.json`. The catalog identifies the required package and materials. The adapter supplies the actual commands and never interprets imported command text.

`klinix build-check` compiles and runs a small C program inside Bubblewrap. Package execution uses the same namespace and mount configuration with the verified inputs mounted read-only. A synthetic root exposes selected host toolchain paths, a minimal device tree, and a temporary filesystem. The build runs offline with a fixed environment, without access to the final target. The upstream package tests run during compilation, and the staged shared executable tests a sample archive before publication.

Klinix copies the local archive and patch into a private workspace and checks the reviewed SHA-256 and the catalog checksum. Archive extraction accepts bounded regular files and directories under the expected source root. Klinix inventories the output after the sandbox exits. It records SHA-256, size, and mode for regular files and the targets of relative symlinks. Link targets must stay within the staging root.

The target must be a fresh directory on a filesystem supporting Linux atomic rename with no replacement. Klinix stages beside the target and publishes only after the build, output validation, and manifest write succeed. If a target appears during the build, it is preserved. Build logs remain in the target's parent. A killed process may leave a private `.klinix-build-*` directory beside the target.

The result is a package staging root with a manifest at `var/lib/klinix/build-manifest.json`. It uses the host compiler and libc. The current engine does not run the other sections of the book-wide plan. A complete base system requires a cross-toolchain, temporary tools, and an isolated target execution context.
