import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from klinix.books.lfs import LfsProvider
from klinix.builder import MANIFEST, build_order, run_build
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


@pytest.mark.parametrize("entry_name", ["package/data.h", "package/uapi/alias/child.h"])
def test_archive_links_are_created_after_regular_members(tmp_path, entry_name):
    archive = tmp_path / "headers.tar.xz"
    with tarfile.open(archive, mode="w:xz") as tar:
        link = tarfile.TarInfo("package/uapi/alias")
        link.type = tarfile.SYMTYPE
        link.linkname = "../data.h"
        tar.addfile(link)
        file = tarfile.TarInfo(entry_name)
        file.size = 3
        tar.addfile(file, io.BytesIO(b"api"))
    destination = tmp_path / "source"
    if entry_name.endswith("/child.h"):
        with pytest.raises(KlinixError, match="Archive symlink conflicts"):
            unpack_archive(archive, destination, "package")
        assert not (destination / "package/data.h").exists()
    else:
        unpack_archive(archive, destination, "package")
        alias = destination / "package/uapi/alias"
        assert alias.is_symlink()
        assert alias.read_bytes() == b"api"


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


def test_xz_extract_preserves_timestamps_and_executable_mode(tmp_path):
    archive = tmp_path / "source.tar.xz"
    with tarfile.open(archive, mode="w:xz") as tar:
        entry = tarfile.TarInfo("package/configure")
        entry.size = 4
        entry.mode = 0o755
        entry.mtime = 1234567890
        tar.addfile(entry, io.BytesIO(b"test"))
    destination = tmp_path / "source"
    unpack_archive(archive, destination, "package")
    script = destination / "package/configure"
    assert script.read_bytes() == b"test"
    assert script.stat().st_mtime == 1234567890
    assert script.stat().st_mode & 0o111


def test_bootstrap_hardlinks_must_stay_inside_target(tmp_path):
    root = tmp_path / "root"
    tools = root / "tools"
    tools.mkdir(parents=True)
    original = tools / "original"
    alias = tools / "alias"
    original.write_text("data")
    os.link(original, alias)
    files = {item["path"]: item for item in inventory(root, (), bootstrap=True)}
    group = [item for item in files.values() if item["kind"] == "hardlink"]
    assert group == [{"path": "tools/original", "kind": "hardlink", "target": "tools/alias"}]
    os.link(original, tmp_path / "outside")
    with pytest.raises(KlinixError, match="hardlink escapes the target"):
        inventory(root, (), bootstrap=True)


def test_sysroot_inventory_allows_only_reviewed_root_entries(tmp_path):
    root = tmp_path / "root"
    (root / "lib64").mkdir(parents=True)
    (root / "usr/lib").mkdir(parents=True)
    (root / "usr/lib/ld-linux-x86-64.so.2").write_text("loader")
    (root / "lib").symlink_to("usr/lib")
    (root / "lib64/ld-linux-x86-64.so.2").symlink_to("../lib/ld-linux-x86-64.so.2")
    layout = {"bootstrap": True, "directories": ("usr", "lib64"), "symlinks": ("lib",)}
    records = {item["path"]: item for item in inventory(root, ("lib",), **layout)}
    assert records["lib"]["target"] == "usr/lib"
    assert records["lib64/ld-linux-x86-64.so.2"]["kind"] == "symlink"
    (root / "lib").unlink()
    (root / "lib").mkdir()
    with pytest.raises(KlinixError, match="Unexpected root entry.*lib"):
        inventory(root, (), **layout)


def test_inventory_keeps_entry_and_permission_checks_independent(tmp_path, monkeypatch):
    from klinix.builder import files

    root = tmp_path / "root"
    (root / "usr").mkdir(parents=True)
    file = root / "usr/file"
    file.write_text("data")
    with monkeypatch.context() as patch:
        patch.setattr(files, "OUTPUT_ENTRY_LIMIT", 1)
        with pytest.raises(KlinixError, match="exceeds the 1-entry limit"):
            inventory(root, ())
    file.chmod(0o4755)
    with pytest.raises(KlinixError, match="Unsupported staged permissions.*usr/file"):
        inventory(root, ())


@pytest.fixture
def pinned_book():
    location = os.environ.get("KLINIX_TEST_LFS_SOURCE")
    if not location or os.environ.get("KLINIX_TEST_LFS_REVISION") != APPROVED_REVISION:
        pytest.skip("local pinned LFS 13.1 Git checkout required")
    return LfsProvider().import_local(Path(location), APPROVED_REVISION)


def test_bootstrap_preview_contains_only_reviewed_commands(pinned_book, tmp_path, capsys):
    db = tmp_path / "catalog.sqlite3"
    Catalog(db).add(pinned_book)
    args = ["--db", str(db), "build-package", "chapter05/gcc-pass1", "--revision", APPROVED_REVISION,
            "--jobs", "3", "--dry-run"]
    assert main(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "dry-run"
    assert output["target_triplet"] == "x86_64-lfs-linux-gnu"
    assert output["sysroot"] == "/mnt/lfs"
    assert output["prerequisites"][0]["recipe"] == "chapter05/binutils-pass1"
    assert {"gmp", "mpfr", "mpc"} == {item["filename"].split("-")[0] for item in output["inputs"]
                                    if not item["filename"].startswith("gcc-")}
    compile_command = next(command for command in output["commands"] if "-j3" in command["argv"])
    assert compile_command["cwd"] == "build"
    assert not list(tmp_path.glob("klinix-build-*.log"))


def test_glibc_plan_schedules_transitive_prerequisites_and_patches(pinned_book, tmp_path, capsys):
    db = tmp_path / "catalog.sqlite3"
    Catalog(db).add(pinned_book)
    build = reviewed_build(pinned_book, "chapter05/glibc", 2)
    assert [stage.package.name for stage in build_order(build)] == [
        "binutils-pass1", "gcc-pass1", "linux-headers", "glibc",
    ]
    assert main([
        "--db", str(db), "build-package", build.recipe, "--revision", APPROVED_REVISION, "--dry-run",
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "dry-run"
    assert output["prerequisites"][0]["prerequisites"][0]["recipe"] == "chapter05/binutils-pass1"
    assert output["prerequisites"][1]["recipe"] == "chapter05/linux-headers"
    assert {item["filename"] for item in output["inputs"]} == {
        "glibc-2.44.tar.xz", "glibc-fhs-1.patch", "glibc-2.44-upstream_fixes-1.patch",
    }
    assert not list(tmp_path.glob("klinix-build-*.log"))


def test_shell_preview_includes_lfs_temporary_tools_in_order(pinned_book, tmp_path, capsys):
    db = tmp_path / "catalog.sqlite3"
    Catalog(db).add(pinned_book)
    build = reviewed_build(pinned_book, "chapter06/bash", 2)
    assert [stage.recipe for stage in build_order(build)] == [
        "chapter05/binutils-pass1", "chapter05/gcc-pass1", "chapter05/linux-headers", "chapter05/glibc",
        "chapter05/libstdc++", "chapter06/m4", "chapter06/ncurses", "chapter06/bash",
    ]
    assert main([
        "--db", str(db), "build-package", build.recipe, "--revision", APPROVED_REVISION, "--dry-run",
    ]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["mode"] == "dry-run"
    assert preview["prerequisites"][0]["recipe"] == "chapter06/ncurses"
    assert preview["inputs"][0]["filename"] == "bash-5.3.tar.gz"
    assert not list(tmp_path.glob("klinix-build-*.log"))


def test_binutils_pass2_preview_covers_the_reviewed_chapter6_order(pinned_book, tmp_path, capsys):
    db = tmp_path / "catalog.sqlite3"
    Catalog(db).add(pinned_book)
    build = reviewed_build(pinned_book, "chapter06/binutils-pass2", 2)
    stages = build_order(build)
    tools = [stage.package.name for stage in stages if stage.recipe.startswith("chapter06/")]
    assert tools == [
        "m4", "ncurses", "bash", "coreutils", "diffutils", "file", "findutils", "gawk",
        "grep", "gzip", "make", "patch", "sed", "tar", "xz", "binutils-pass2",
    ]
    file = next(stage for stage in stages if stage.recipe == "chapter06/file")
    assert any(
        command.cwd == "build-host" and command.argv[0] == "../configure"
        for command in file.commands
    )
    assert any(
        f"FILE_COMPILE=/work/source/{file.source_root}/build-host/src/file" in command.argv
        for command in file.commands
    )
    assert main([
        "--db", str(db), "build-package", build.recipe, "--revision", APPROVED_REVISION, "--dry-run",
    ]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["mode"] == "dry-run"
    assert preview["prerequisites"][0]["recipe"] == "chapter06/xz"
    first_pass = next(stage for stage in stages if stage.recipe == "chapter05/binutils-pass1")
    assert first_pass.inputs[0] == build.inputs[0]
    assert not list(tmp_path.glob("klinix-build-*.log"))


def test_gcc_pass2_plans_a_separate_native_verification(pinned_book, tmp_path, capsys):
    db = tmp_path / "catalog.sqlite3"
    Catalog(db).add(pinned_book)
    build = reviewed_build(pinned_book, "chapter06/gcc-pass2", 2)
    stages = build_order(build)
    assert [stage.recipe for stage in stages[-2:]] == ["chapter06/binutils-pass2", "chapter06/gcc-pass2"]
    assert build.inputs == next(stage.inputs for stage in stages if stage.recipe == "chapter05/gcc-pass1")
    assert main([
        "--db", str(db), "build-package", build.recipe, "--revision", APPROVED_REVISION, "--dry-run",
    ]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["mode"] == "dry-run"
    target_commands = [command for command in preview["commands"] if command["target_root"]]
    assert any("--disable-libstdcxx-pch" in command["argv"] for command in preview["commands"])
    assert len(target_commands) == 1
    assert target_commands[0]["cwd"] == "/tmp"
    assert "std::thread" in target_commands[0]["argv"][2]
    assert not list(tmp_path.glob("klinix-build-*.log"))


def test_target_verification_exposes_only_the_staged_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "tmp").mkdir(parents=True)
    (root / "dev").mkdir()
    sandbox = Bubblewrap.__new__(Bubblewrap)
    sandbox._executable = "/usr/bin/bwrap"
    argv = ("/usr/bin/cc", "--version")
    log = io.BytesIO()

    def capture(args, command, output, *, timeout):
        i = args.index("--ro-bind")
        assert args[i:i + 3] == ["--ro-bind", str(root), "/"]
        assert args.count("--ro-bind") == 1 and "--bind" not in args
        assert args[args.index("--dev") + 1] == "/dev"
        assert args[args.index("--tmpfs") + 1] == "/tmp"
        assert args[args.index("--chdir") + 1] == "/tmp"
        assert "/usr/bin:/usr/sbin" in args
        assert "--unshare-all" in args and "--clearenv" in args
        assert command == argv and output is log and timeout == 10
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(sandbox, "_execute", capture)
    sandbox.run_in_target(root, argv, log, timeout=10)
    assert log.getvalue() == b"$ /usr/bin/cc --version\n"

    (root / "tmp").rmdir()
    (root / "tmp").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(KlinixError, match="Target verification requires a real directory"):
        sandbox.run_in_target(root, argv, log, timeout=10)


def test_libstdcxx_uses_the_same_verified_gcc_archive(pinned_book, tmp_path, monkeypatch):
    from klinix import builder

    class ProbeOnly:
        def __init__(self, *, bootstrap):
            assert bootstrap

        def probe(self):
            pass

    build = reviewed_build(pinned_book, "chapter05/libstdc++", 2)
    staged_names = []
    gcc_inodes = []

    def stage_input(source, destination, approved):
        staged_names.append(source.name)
        destination.write_text(approved.sha256)

    def inspect_inputs(stage, _sandbox, work, inputs, _log):
        (work / "source").mkdir()
        if stage.recipe in {"chapter05/gcc-pass1", "chapter05/libstdc++"}:
            name = stage.inputs[0].filename
            gcc_inodes.append((inputs / name).stat().st_ino)
            assert (inputs / name).read_text() == stage.inputs[0].sha256
        if stage.recipe == build.recipe:
            raise KlinixError("test reached libstdc++")

    monkeypatch.setattr(builder, "Bubblewrap", ProbeOnly)
    monkeypatch.setattr(builder, "stage_input", stage_input)
    monkeypatch.setattr(builder, "_run_stage", inspect_inputs)
    target = tmp_path / "target"
    with pytest.raises(KlinixError, match=r"test reached libstdc\+\+"):
        run_build(build, tmp_path, target)
    assert staged_names.count(build.inputs[0].filename) == 1
    assert len(gcc_inodes) == 2
    assert gcc_inodes[0] == gcc_inodes[1]
    assert not target.exists()


def test_bootstrap_preverifies_later_materials(pinned_book, tmp_path, monkeypatch):
    from klinix import builder

    class ProbeOnly:
        def __init__(self, *, bootstrap):
            assert bootstrap

        def probe(self):
            pass

    monkeypatch.setattr(builder, "Bubblewrap", ProbeOnly)
    build = reviewed_build(pinned_book, "chapter05/gcc-pass1", 1)
    materials = tmp_path / "materials"
    materials.mkdir()
    staged = []

    def stage_input(path, _dest, approval):
        assert path.name == approval.filename
        staged.append(approval.filename)
        if approval.unpack_as == "mpc":
            raise KlinixError("invalid final dependency")

    monkeypatch.setattr(builder, "stage_input", stage_input)
    target = tmp_path / "target"
    with pytest.raises(KlinixError, match="invalid final dependency.*Build log"):
        run_build(build, materials, target)
    assert len(staged) == 5
    assert not target.exists()


@pytest.mark.parametrize("recipe", ["chapter05/binutils-pass1", "chapter05/gcc-pass1"])
def test_real_bootstrap_pass(recipe, pinned_book, tmp_path, capsys):
    location = os.environ.get("KLINIX_TEST_BOOTSTRAP_SOURCES")
    if not location or (recipe.endswith("gcc-pass1") and not os.environ.get("KLINIX_TEST_GCC")):
        pytest.skip("official bootstrap materials and rootless Bubblewrap required")
    db = tmp_path / "catalog.sqlite3"
    Catalog(db).add(pinned_book)
    target = tmp_path / "sysroot"
    assert main(["--db", str(db), "build-package", recipe, "--revision", APPROVED_REVISION,
                 "--sources", location, "--target", str(target), "--jobs", "2"]) == 0
    output = json.loads(capsys.readouterr().out)
    manifest = json.loads((target / MANIFEST).read_text())
    triplet = "x86_64-lfs-linux-gnu"
    assert manifest["target_triplet"] == triplet
    assert manifest["sysroot"] == "/mnt/lfs"
    assert manifest["recipe"] == recipe
    assert (target / f"tools/bin/{triplet}-as").is_file()
    assert (target / f"tools/bin/{triplet}-ld").is_file()
    log = Path(output["log"]).read_text()
    assert "Advanced Micro Devices X86-64" in log
    if recipe.endswith("gcc-pass1"):
        assert manifest["prerequisites"][0]["recipe"] == "chapter05/binutils-pass1"
        assert (target / f"tools/bin/{triplet}-gcc").is_file()
        assert (target / f"tools/bin/{triplet}-g++").is_file()
        assert "Klinix binutils-pass1-v1" in log.split("Klinix gcc-pass1-v1", 1)[0]
        assert "File: cxx.o" in log


def test_real_cross_glibc(pinned_book, tmp_path, capsys):
    location = os.environ.get("KLINIX_TEST_SYSROOT_SOURCES")
    if not location:
        pytest.skip("official sysroot materials and rootless Bubblewrap required")
    db = tmp_path / "catalog.sqlite3"
    Catalog(db).add(pinned_book)
    target = tmp_path / "sysroot"
    assert main([
        "--db", str(db), "build-package", "chapter05/glibc", "--revision", APPROVED_REVISION,
        "--sources", location, "--target", str(target), "--jobs", "2",
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    manifest = json.loads((target / MANIFEST).read_text())
    assert manifest["recipe"] == "chapter05/glibc"
    assert (target / "usr/include/linux/version.h").is_file()
    assert (target / "usr/lib/libc.so.6").is_file()
    loader = (target / "lib64/ld-linux-x86-64.so.2").resolve(strict=True)
    assert loader.is_relative_to(target)
    log = Path(output["log"]).read_text()
    assert "Requesting program interpreter: /lib64/ld-linux-x86-64.so.2" in log
    assert "libc.so.6 => /mnt/lfs/usr/lib/libc.so.6" in log
    assert "Klinix sysroot OK" in log.splitlines()


def test_real_temporary_shell(pinned_book, tmp_path, capsys):
    location = os.environ.get("KLINIX_TEST_SHELL_SOURCES")
    if not location:
        pytest.skip("official temporary shell materials and rootless Bubblewrap required")
    db = tmp_path / "catalog.sqlite3"
    Catalog(db).add(pinned_book)
    target = tmp_path / "sysroot"
    assert main([
        "--db", str(db), "build-package", "chapter06/bash", "--revision", APPROVED_REVISION,
        "--sources", location, "--target", str(target), "--jobs", "2",
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert (target / "bin/sh").resolve() == (target / "usr/bin/bash").resolve()
    assert (target / "usr/lib/libstdc++.so.6").is_file()
    assert (target / "usr/bin/m4").is_file()
    assert (target / "tools/bin/tic").is_file()
    assert (target / "usr/bin/infocmp").is_file()
    log = Path(output["log"]).read_text()
    assert "libstdc++.so.6 => /mnt/lfs/usr/lib/libstdc++.so.6" in log
    assert {"Klinix C++ runtime OK", "Klinix M4 OK", "Klinix target shell OK"}.issubset(log.splitlines())


def test_real_temporary_tools_through_binutils_pass2(pinned_book, tmp_path, capsys):
    location = os.environ.get("KLINIX_TEST_CHAPTER6_SOURCES")
    if not location:
        pytest.skip("official Chapter 6 materials and rootless Bubblewrap required")
    db = tmp_path / "catalog.sqlite3"
    Catalog(db).add(pinned_book)
    target = tmp_path / "sysroot"
    assert main([
        "--db", str(db), "build-package", "chapter06/binutils-pass2", "--revision", APPROVED_REVISION,
        "--sources", location, "--target", str(target), "--jobs", "2",
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert (target / "sbin/chroot").resolve() == (target / "usr/sbin/chroot").resolve()
    assert (target / "usr/share/misc/magic.mgc").is_file()
    assert (target / "usr/bin/ld").is_file()
    assert (target / "tools/bin/x86_64-lfs-linux-gnu-ld").is_file()
    assert not (target / "usr/lib/libmagic.la").exists()
    assert not (target / "usr/lib/liblzma.la").exists()
    log = Path(output["log"]).read_text()
    assert json.loads((target / MANIFEST).read_text())["recipe"] == "chapter06/binutils-pass2"
    assert "Advanced Micro Devices X86-64" in log.split("Klinix binutils-pass2-v1", 1)[1]


def test_real_native_gcc(pinned_book, tmp_path, capsys):
    location = os.environ.get("KLINIX_TEST_GCC_PASS2_SOURCES")
    if not location:
        pytest.skip("official GCC pass 2 materials and rootless Bubblewrap required")
    db = tmp_path / "catalog.sqlite3"
    Catalog(db).add(pinned_book)
    target = tmp_path / "sysroot"
    assert main([
        "--db", str(db), "build-package", "chapter06/gcc-pass2", "--revision", APPROVED_REVISION,
        "--sources", location, "--target", str(target), "--jobs", "2",
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert (target / "usr/bin/cc").readlink() == Path("gcc")
    assert (target / "usr/lib/libgcc_s.so.1").is_file()
    assert not list((target / "tmp").iterdir())
    assert not list((target / "dev").iterdir())
    log = Path(output["log"]).read_text()
    assert {"Klinix native C OK", "Klinix native C++ thread and exception OK"}.issubset(log.splitlines())
