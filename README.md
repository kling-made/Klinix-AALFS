# Klinix

Klinix is an independent Advanced Automated Linux From Scratch builder and source package manager for x86-64 Linux and systemd.

The initial release is a headless CLI. It discovers official LFS releases, imports a pinned systemd Git book into SQLite, and generates a dry-run plan with source and patch checksums, bootstrap stages, and reference commands. The graphical builder, package installation, and bootable system builds are future milestones.

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

## Develop

```sh
python -m pip install -e '.[test]'
python -m pytest
```

See the [project specification](docs/specification.md), [architecture](docs/architecture.md), [testing strategy](docs/testing.md), and [roadmap](docs/roadmap.md). Klinix is licensed under GPL-3.0-or-later. The [third-party notices](THIRD_PARTY_NOTICES.md) cover the upstream books and extracted instructions.
