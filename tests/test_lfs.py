import os
from pathlib import Path

import pytest

from conftest import git, section, write
from klinix.books.lfs import LfsProvider
from klinix.errors import KlinixError


def test_book_import_uses_git_objects_and_filters_edition(book_checkout) -> None:
    root, revision = book_checkout
    (root / "packages.ent").write_text("uncommitted changes")
    book = LfsProvider().import_local(root, revision)
    assert book.pin.version == "99.1-systemd"
    assert book.pin.revision == revision
    assert [recipe.id for recipe in book.recipes] == [
        "chapter05/core", "chapter08/core", "chapter08/systemd",
    ]
    first, final, systemd = book.recipes
    assert first.package.version == "1.2.3"
    assert "core-1.2.3-fix.patch" in first.steps[0].text
    assert final.steps[0].manual
    assert len(final.steps) == 1
    assert systemd.steps[0].text == "printf '99.1-systemd\xa0&'"
    assert "Copyright © 1999-2026 upstream test authors" in book.instruction_license
    assert book.artifacts[-1].algorithm == "sha256"


def test_release_and_development_metadata(book_checkout) -> None:
    root, old_revision = book_checkout
    git(root, "commit", "--allow-empty", "-qm", "Book development commit")
    with pytest.raises(KlinixError, match="Expected Git revision"):
        LfsProvider().import_local(root, old_revision)
    book = LfsProvider().import_local(root)
    new_revision = git(root, "rev-parse", "HEAD")
    assert book.pin.version == f"development-{new_revision[:12]}-systemd"
    assert new_revision[:12] in book.recipes[-1].steps[0].text


def test_discovery_sorts_stable_refs_and_uses_peeled_tag(monkeypatch) -> None:
    from klinix.books import git as git_backend

    def ls_remote(*_args):
        return (
            "ref: refs/heads/trunk\tHEAD\n" + "f" * 40 + "\tHEAD\n"
            + "c" * 40 + "\trefs/tags/r99.2\n"
            + "a" * 40 + "\trefs/tags/r99.10\n"
            + "b" * 40 + "\trefs/tags/r99.10^{}\n"
            + "e" * 40 + "\trefs/tags/r99.11-rc1\n"
        ).encode()

    monkeypatch.setattr(git_backend, "_git", ls_remote)
    releases = LfsProvider().discover()
    assert [release.version for release in releases] == [
        "development-ffffffffffff-systemd", "99.10-systemd", "99.2-systemd",
    ]
    assert releases[1].revision == "b" * 40


def test_include_traversal_is_rejected(book_checkout) -> None:
    root, _ = book_checkout
    write(root, "chapter05/chapter05.xml", """
      <chapter>
        <xi:include xmlns:xi="http://www.w3.org/2001/XInclude" href="../chapter08/core.xml"/>
      </chapter>
    """)
    git(root, "commit", "-qam", "Bad include")
    with pytest.raises(KlinixError, match="Unsupported XML include"):
        LfsProvider().import_local(root)


def test_external_general_entity_is_rejected_before_reading(book_checkout, tmp_path: Path) -> None:
    root, _ = book_checkout
    secret = tmp_path / "private.xml"
    secret.write_text("fixture-must-not-be-disclosed")
    filename = "chapter05/core.xml"
    data = section("core", "<screen><userinput>&local;</userinput></screen>")
    write(root, filename, data.replace("]>", f'<!ENTITY local SYSTEM "{secret.as_uri()}">]>'))
    git(root, "commit", "-qam", "Bad entity")
    with pytest.raises(KlinixError, match="External general entities") as error:
        LfsProvider().import_local(root)
    assert "fixture-must-not-be-disclosed" not in str(error.value)


def test_committed_symlink_is_not_read(book_checkout) -> None:
    root, _ = book_checkout
    (root / "chapter05/core.xml").unlink()
    (root / "chapter05/core.xml").symlink_to("../chapter08/core.xml")
    git(root, "commit", "-qam", "Bad symlink")
    with pytest.raises(KlinixError, match="Missing regular book file"):
        LfsProvider().import_local(root)


@pytest.mark.skipif(not os.environ.get("KLINIX_TEST_LFS_SOURCE"), reason="official Git checkout not set")
def test_pinned_official_book() -> None:
    root = Path(os.environ["KLINIX_TEST_LFS_SOURCE"])
    revision = os.environ["KLINIX_TEST_LFS_REVISION"]
    book = LfsProvider().import_local(root, revision)
    assert book.pin.revision == revision
    assert book.pin.version.endswith("-systemd")
    assert len(book.recipes) >= 100
    assert len(book.artifacts) >= 75
    ids = [recipe.id for recipe in book.recipes]
    assert ids.index("chapter05/binutils-pass1") < ids.index("chapter06/binutils-pass2")
    assert ids.index("chapter06/binutils-pass2") < ids.index("chapter08/binutils")
    assert "chapter08/systemd" in ids
    assert "chapter08/sysvinit" not in ids
    assert "chapter10/kernel" in ids
    bzip2 = book.recipes[ids.index("chapter08/bzip2")]
    assert bzip2.package.name == "bzip2"
    assert any("bzip2" in url and url.endswith(".patch") for url in bzip2.materials)
    assert any(item.kind == "source" and item.algorithm == "md5" for item in book.artifacts)
    assert "Gerard Beekmans" in book.instruction_license
