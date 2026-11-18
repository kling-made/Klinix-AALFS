import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from klinix.books.lfs import LfsProvider
from klinix.builder import MANIFEST, run_build
from klinix.builder.files import inventory, new_target, publish, stage_input, unpack_archive
from klinix.builder.recipes import ApprovedInput, reviewed_build
from klinix.builder.sandbox import Bubblewrap
from klinix.catalog import Catalog
from klinix.cli import main
from klinix.errors import KlinixError
from klinix.recipes import Artifact

APPROVED_REVISION = "2e45031d5a2c1fe9e77239667f70631580eebfc3"
RECIPE = "chapter08/bzip2"


def test_unreviewed_catalog_cannot_run(book_checkout, tmp_path, capsys):
    book = LfsProvider().import_local(book_checkout[0])
    db = tmp_path / "catalog.sqlite3"
    Catalog(db).add(book)
    assert main(["--db", str(db), "build-package", RECIPE, "--revision", book.pin.revision, "--dry-run"]) == 1
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("name, entry_type", [
    ("package/../../escape", tarfile.REGTYPE),
    ("/package/escape", tarfile.REGTYPE),
    ("package/link", tarfile.SYMTYPE),
    ("package/hard-link", tarfile.LNKTYPE),
])
def test_archive_unsafe_members_fail(tmp_path, name, entry_type):
    archive = tmp_path / "archive.tar.gz"
    with tarfile.open(archive, mode="w:gz") as tar:
        entry = tarfile.TarInfo(name)
        entry.type = entry_type
        entry.linkname = "../../escape"
        tar.addfile(entry, io.BytesIO(b""))
    with pytest.raises(KlinixError, match="Archive path escapes|Unsupported archive entry"):
        unpack_archive(archive, tmp_path / "source", "package")
    assert not (tmp_path / "escape").exists()


def test_verified_material_checks_sha256_and_catalog_checksum(tmp_path):
    path = tmp_path / "package.tar.gz"
    content = b"known package"
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    correct = Artifact("source", "source", "https://example.invalid/package.tar.gz", "sha256", digest)
    stage_input(path, tmp_path / "correct", ApprovedInput(correct, digest))
    assert (tmp_path / "correct").read_bytes() == content
    with pytest.raises(KlinixError, match="reviewed SHA-256"):
        stage_input(path, tmp_path / "bad-approval", ApprovedInput(correct, "0" * 64))
    wrong = Artifact("source", "source", correct.url, "sha256", "0" * 64)
    with pytest.raises(KlinixError, match="SHA256 checksum mismatch"):
        stage_input(path, tmp_path / "bad-catalog", ApprovedInput(wrong, digest))


def test_manifest_rejects_unsafe_links_and_hardlinks(tmp_path):
    root = tmp_path / "root"
    usr = root / "usr"
    usr.mkdir(parents=True)
    source = usr / "source"
    source.write_text("data", encoding="utf-8")
    link = usr / "link"
    link.symlink_to(Path("source"))
    assert inventory(root, ("usr/source", "usr/link"))[0] == {
        "path": "usr/link", "kind": "symlink", "target": "source",
    }
    link.unlink()
    link.symlink_to("../../escape")
    with pytest.raises(KlinixError, match="escapes the target"):
        inventory(root, ())
    link.unlink()
    os.link(source, link)
    with pytest.raises(KlinixError, match="Unsupported staged file type"):
        inventory(root, ())


def test_directory_publication_does_not_replace_target(tmp_path):
    target = new_target(tmp_path / "target")
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "own").write_text("own")
    target.mkdir()
    (target / "marker").write_text("concurrent")
    with pytest.raises(KlinixError, match="Target appeared"):
        publish(stage, target)
    assert (target / "marker").read_text() == "concurrent"
    assert (stage / "own").is_file()
    with pytest.raises(KlinixError, match="Target already exists"):
        new_target(target)
    published = new_target(tmp_path / "new-target")
    publish(stage, published)
    assert (published / "own").is_file()
    assert not stage.exists()


@pytest.fixture
def real_build_inputs():
    if not os.environ.get("KLINIX_TEST_BUILD_SOURCES"):
        pytest.skip("official source directory and rootless Bubblewrap required")
    checkout = Path(os.environ["KLINIX_TEST_LFS_SOURCE"])
    sources = Path(os.environ["KLINIX_TEST_BUILD_SOURCES"])
    book = LfsProvider().import_local(checkout, APPROVED_REVISION)
    return book, sources


def test_build_preview_and_real_bzip2_build(real_build_inputs, tmp_path, capsys):
    book, sources = real_build_inputs
    db = tmp_path / "catalog.sqlite3"
    Catalog(db).add(book)
    target = tmp_path / "new target"
    command = [
        "--db", str(db), "build-package", RECIPE, "--revision", APPROVED_REVISION,
        "--sources", str(sources), "--target", str(target), "--jobs", "2",
    ]
    assert main([*command, "--dry-run"]) == 0
    draft = json.loads(capsys.readouterr().out)
    assert draft["mode"] == "dry-run"
    assert draft["adapter"] == "bzip2-v1"
    assert not target.exists()
    assert not list(tmp_path.glob("klinix-build-*.log"))
    assert main(command) == 0
    result = json.loads(capsys.readouterr().out)
    manifest = json.loads((target / MANIFEST).read_text())
    assert result["files"] == len(manifest["files"]) >= 20
    assert result["book"]["revision"] == APPROVED_REVISION
    assert manifest["package"]["name"] == "bzip2"
    records = {record["path"]: record for record in manifest["files"]}
    assert records["usr/bin/bzcat"]["target"] == "bzip2"
    assert records["usr/lib/libbz2.so"]["kind"] == "symlink"
    assert hashlib.sha256((target / "usr/bin/bzip2").read_bytes()).hexdigest() == records["usr/bin/bzip2"]["sha256"]
    assert bytes((target / "usr/bin/bzip2").read_bytes()[:4]) == b"\x7fELF"
    log = Path(result["log"]).read_text()
    assert "cmp sample1.bz2 sample1.rb2" in log
    assert "bin/bzip2 -t sample1.bz2" in log
    assert main(command) == 1
    assert (target / MANIFEST).is_file()


def test_reviewed_build_bad_input_preserves_log_without_target(real_build_inputs, tmp_path):
    book, _ = real_build_inputs
    build = reviewed_build(book, RECIPE, 1)
    bad_source = tmp_path / build.inputs[0].filename
    bad_source.write_bytes(b"unreviewed input")
    target = tmp_path / "target"
    with pytest.raises(KlinixError, match="reviewed SHA-256.*Build log:"):
        run_build(build, tmp_path, target)
    assert not target.exists()
    assert len(list(tmp_path.glob("klinix-build-*.log"))) == 1
    assert not list(tmp_path.glob(".klinix-build-*"))


def test_sandbox_hides_host_paths_and_mounts_inputs_read_only(real_build_inputs, tmp_path, monkeypatch):
    monkeypatch.setenv("KLINIX_TEST_SECRET", "host value")
    work = tmp_path / "work"
    inputs = tmp_path / "inputs"
    work.mkdir()
    inputs.mkdir()
    (inputs / "source").write_text("verified bytes")
    script = """
import errno
import os
from pathlib import Path
assert 'KLINIX_TEST_SECRET' not in os.environ
assert not Path('/etc/passwd').exists()
assert not Path('/proc/1').exists()
assert Path('/inputs/source').read_text() == 'verified bytes'
try:
    Path('/inputs/source').write_text('tampered')
except OSError as error:
    assert error.errno == errno.EROFS
else:
    raise AssertionError('inputs are writable')
Path('/work/output').write_text('staged')
"""
    with (tmp_path / "sandbox.log").open("wb") as log:
        Bubblewrap().run(work, "/work", inputs, ("/usr/bin/python3", "-c", script), log)
    assert (work / "output").read_text() == "staged"
    assert (inputs / "source").read_text() == "verified bytes"
