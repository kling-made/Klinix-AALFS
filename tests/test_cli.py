import json
import sqlite3
from pathlib import Path

import pytest

from conftest import git, section, write
from klinix.cli import main
from klinix.config import Settings
from klinix.errors import KlinixError


def test_cli_import_list_plan_and_inspect(book_checkout, tmp_path: Path, capsys) -> None:
    root, revision = book_checkout
    config = tmp_path / "project.toml"
    config.write_text('database = "catalog.sqlite3"\nrecommended = true\n')
    opts = ["--config", str(config)]
    assert main(opts + ["books", "import", str(root), "--expect-revision", revision]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["added"] is True
    assert summary["packages"] == 2
    assert main(opts + ["books", "list"]) == 0
    assert json.loads(capsys.readouterr().out) == [summary["book"]]
    assert main(opts + ["packages", "--revision", revision]) == 0
    assert json.loads(capsys.readouterr().out)[-1]["name"] == "systemd"
    assert main(opts + ["sources", "--revision", revision]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 3
    assert main(opts + ["plan", "--revision", revision, "--no-recommended", "systemd"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "dry-run"
    assert result["policy"] == {"recommended": False, "optional": False}
    assert len(result["recipes"]) == 3
    assert (tmp_path / "catalog.sqlite3").is_file()


def test_dry_run_does_not_run_reference_steps(book_checkout, tmp_path: Path, capsys) -> None:
    root, _ = book_checkout
    sentinel = tmp_path / "a-command-ran"
    write(root, "chapter05/core.xml", section(
        "core", f"<screen><userinput>touch '{sentinel}'</userinput></screen>",
    ))
    git(root, "commit", "-qam", "Dry-run command fixture")
    revision = git(root, "rev-parse", "HEAD")
    opts = ["--db", str(tmp_path / "catalog.sqlite3")]
    assert main(opts + ["books", "import", str(root)]) == 0
    capsys.readouterr()
    assert main(opts + ["plan", "--revision", revision]) == 0
    result = json.loads(capsys.readouterr().out)
    assert str(sentinel) in result["recipes"][0]["steps"][0]["text"]
    assert not sentinel.exists()


def test_config_validates_types_and_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('recommended = "yes"\n')
    with pytest.raises(KlinixError, match="must be booleans"):
        Settings.load(path)
    path.write_text("flag = true\n")
    with pytest.raises(KlinixError, match="Unknown configuration options"):
        Settings.load(path)


def test_catalog_import_rejects_unrelated_database(book_checkout, tmp_path: Path) -> None:
    path = tmp_path / "other.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated (value TEXT)")
    assert main(["--db", str(path), "books", "import", str(book_checkout[0])]) == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_schema").fetchall() == [("unrelated",)]


def test_source_verification_uses_book_checksum(book_checkout, tmp_path: Path, capsys) -> None:
    root, revision = book_checkout
    opts = ["--db", str(tmp_path / "catalog.sqlite3")]
    assert main(opts + ["books", "import", str(root)]) == 0
    capsys.readouterr()
    source = tmp_path / "systemd-42.tar.xz"
    source.write_bytes(b"data")
    command = opts + ["verify-source", "--revision", revision, str(source)]
    assert main(command) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["matched"]
    assert result["bytes"] == 4
    assert result["book"]["revision"] == revision
    assert result["material"]["algorithm"] == "md5"
    source.write_bytes(b"changed")
    assert main(command) == 1
    assert capsys.readouterr().out == ""


def test_source_verification_rejects_symlinks(book_checkout, tmp_path: Path) -> None:
    from klinix.books.lfs import LfsProvider
    from klinix.sources import select_artifact, verify_file

    book = LfsProvider().import_local(book_checkout[0])
    archive = tmp_path / "systemd-42.tar.xz"
    target = tmp_path / "different-name"
    target.write_bytes(b"data")
    archive.symlink_to(target)
    artifact = select_artifact(book, archive)
    assert verify_file(target, select_artifact(book, target, artifact.url)) == 4
    with pytest.raises(KlinixError, match="Cannot read source"):
        verify_file(archive, artifact)
