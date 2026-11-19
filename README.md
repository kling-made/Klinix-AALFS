# Klinix

Klinix is an independent Advanced Automated Linux From Scratch builder and source package manager for x86-64 Linux and systemd.

The headless CLI discovers official LFS releases, imports a pinned systemd Git book into SQLite, and generates a dry-run plan with source and patch checksums, bootstrap stages, and reference commands. It builds a new LFS 13.1 sysroot through the Chapter 6 temporary tools and verifies the native C and C++ compilers inside the staged root. It can also stage Bzip2 in a separate directory. The graphical builder, complete base system, and installed package manager are future milestones.

## Install

Python 3.11 or later and Git are required.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
klinix --help
```

## Inspect a pinned LFS build

Run `klinix books discover` to see available releases and commit IDs. This example uses a verified LFS 13.1 systemd source revision.

```sh
git clone --depth 1 --branch r13.1 https://git.linuxfromscratch.org/lfs.git lfs-book
revision=2e45031d5a2c1fe9e77239667f70631580eebfc3
klinix --db ./catalog.sqlite3 books import ./lfs-book --expect-revision "$revision"
klinix --db ./catalog.sqlite3 books list
klinix --db ./catalog.sqlite3 packages --revision "$revision"
klinix --db ./catalog.sqlite3 sources --revision "$revision"
klinix --db ./catalog.sqlite3 plan --revision "$revision" > plan.json
```

Pass a package name or a stage ID such as `chapter05/binutils-pass1` after the revision to show the selected stage and its prerequisites. Plans preserve the book's sequence and display reference text. Imported text is never executed.

To check an archive or patch you've already downloaded, run `klinix --db ./catalog.sqlite3 verify-source --revision "$revision" path/to/file`. The command matches the published checksum and accepts `--url` to identify a renamed file.

The default catalog lives under `$XDG_STATE_HOME/klinix` or `~/.local/state/klinix`. Use `--config path.toml` to set `database`, `recommended`, and `optional`. Relative database paths resolve from the configuration file's directory. Explicit CLI flags take precedence.

## Bootstrap the cross-toolchain and temporary tools

Use the LFS 13.1 revision imported above. `build-check --bootstrap` checks the unprivileged x86-64 build environment and host C++ toolchain. Keep several GB available in the target's parent directory. Both GCC passes compile from source.

```sh
klinix build-check --bootstrap
klinix --db ./catalog.sqlite3 build-package chapter06/gcc-pass2 --revision "$revision" --dry-run
klinix -v --db ./catalog.sqlite3 build-package chapter06/gcc-pass2 --revision "$revision" \
  --sources ./materials --target ./lfs-sysroot --jobs 2
```

Use the filenames and URLs in the preview's `inputs` and nested `prerequisites` to populate `materials`. The combined root contains the cross tools under `tools/`, the target-native compilers and temporary tools under `usr/`, and a manifest under `var/lib/klinix/`. Use an earlier recipe ID such as `chapter05/glibc`, `chapter06/bash`, or `chapter06/binutils-pass2` to stop after that stage. [Directory build details](docs/directory-build.md)

## Build the reviewed Bzip2 package

Use the imported LFS 13.1 revision above. The build requires an unprivileged x86-64 Linux account, Bubblewrap with user namespaces enabled, GCC and its development libraries, binutils, make, patch, sed, and coreutils. The compiler and libc come from the host toolchain exposed read-only inside the sandbox.

```sh
klinix build-check
klinix --db ./catalog.sqlite3 build-package chapter08/bzip2 --revision "$revision" --dry-run

mkdir -p ./materials
curl --fail --location --output ./materials/bzip2-1.0.8.tar.gz \
  https://www.sourceware.org/pub/bzip2/bzip2-1.0.8.tar.gz
curl --fail --location --output ./materials/bzip2-1.0.8-install_docs-1.patch \
  https://www.linuxfromscratch.org/patches/lfs/13.1/bzip2-1.0.8-install_docs-1.patch
klinix --db ./catalog.sqlite3 build-package chapter08/bzip2 --revision "$revision" \
  --sources ./materials --target ./bzip2-root --jobs 2
```

Choose a target name that does not exist. The command prints the target path, its `var/lib/klinix/build-manifest.json`, and the path to the build log. This is a single-package staging root. The [directory build specification](docs/directory-build.md) describes its output and isolation.

## Develop

```sh
python -m pip install -e '.[test]'
python -m pytest
```

See the [project specification](docs/specification.md), [architecture](docs/architecture.md), [testing strategy](docs/testing.md), and [roadmap](docs/roadmap.md). Klinix is licensed under GPL-3.0-or-later. The [third-party notices](THIRD_PARTY_NOTICES.md) cover the upstream books and extracted instructions.
