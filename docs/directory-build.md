# Reviewed directory build

The Bzip2, Binutils pass 1, and GCC pass 1 adapters implement reviewed sections of LFS 13.1 systemd. The exact book revision and reviewed SHA-256 digests are in `src/klinix/builder/approvals.json`. The catalog identifies the required packages and materials. The adapters supply the actual commands and never interpret imported command text.

`klinix build-check` compiles and runs a small C program inside Bubblewrap. With `--bootstrap`, it checks the additional host programs and compiles and runs a small C++ program with the bootstrap mount layout. Package execution uses the same namespace and mount configuration with the verified inputs mounted read-only. A synthetic root exposes selected host toolchain paths, a minimal device tree, and a temporary filesystem. The build runs offline with a fixed environment, without access to the final target.

Klinix copies all requested source archives and patches into a private workspace and checks the reviewed SHA-256 and the catalog checksum before running the first stage. Archive extraction accepts bounded regular files and directories under the expected source root. Klinix inventories the output after the sandbox exits. It records SHA-256, size, and mode for regular files, the targets of relative symlinks, and hardlink groups contained in the bootstrap root.

The target must be a fresh directory on a filesystem supporting Linux atomic rename with no replacement. Klinix stages beside the target and publishes only after the build, output validation, and manifest write succeed. If a target appears during the build, it is preserved. Build logs remain in the target's parent. A killed process may leave a private `.klinix-build-*` directory beside the target.

The Bzip2 adapter runs the upstream tests and tests a sample archive with the staged shared executable. Its staging root contains `usr/` and uses the host compiler and libc.

For the bootstrap passes, the private target is mounted at `/mnt/lfs`. Binutils installs the `x86_64-lfs-linux-gnu` assembler, linker, and related tools under `/mnt/lfs/tools`, then assembles and links a small ELF. The GCC recipe runs Binutils first and uses those tools to build the cross C and C++ front ends and `libgcc`. It omits debug information from host-side compiler objects to reduce workspace use. The staged compiler verifies the target triplet and sysroot and compiles freestanding C and C++ objects before publication. Later bootstrap operations should mount the sysroot at the same `/mnt/lfs` path.

Both layouts contain a manifest at `var/lib/klinix/build-manifest.json`. Linux API headers, Glibc, temporary tools, and a bootable base system are pending.
