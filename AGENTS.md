# Klinix development

Klinix is an independent x86-64, systemd-only Linux From Scratch builder and source package manager. Follow the project specification and milestone status in [docs](docs/roadmap.md).

- Keep Python backend models, providers, catalog, and planning independent of the CLI and the future PySide6 interface.
- Use typed interfaces, parameterized SQLite queries, and transactional migrations. Preserve pinned book revisions and deterministic recipe IDs.
- Treat external book XML, source archives, and recipe data as untrusted. Preserve upstream licenses. Require a reviewed execution adapter and an isolated target before enabling build operations.
- Run `python -m pip install -e '.[test]'`, then `python -m pytest`. Use temporary paths in tests. Keep network, root, chroot, and QEMU tests opt-in and report which were run.
- Update [CHANGELOG.md](CHANGELOG.md) and [docs/roadmap.md](docs/roadmap.md) with material changes and verified scope.
