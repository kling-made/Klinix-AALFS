"""Run a reviewed command in a rootless Bubblewrap sandbox."""

import logging
import os
import platform
import shlex
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO

from klinix.builder.recipes import BOOTSTRAP_HOST_TOOLS, SYSROOT, TOOLS
from klinix.errors import KlinixError

LOGGER = logging.getLogger(__name__)
TOOL_PATH = "/usr/bin:/bin"
TOOLCHAIN = ("/usr/bin", "/usr/include", "/usr/lib", "/usr/lib64", "/usr/libexec", "/usr/share")
REQUIRED = ("gcc", "ar", "ranlib", "make", "patch", "sed", "cp", "ln", "rm", "install", "env", "cmp", "sh")


class Bubblewrap:
    def __init__(self, *, bootstrap: bool = False) -> None:
        if platform.system() != "Linux" or platform.machine() != "x86_64" or os.geteuid() == 0:
            raise KlinixError("Directory package builds require an unprivileged x86-64 Linux user")
        executable = shutil.which("bwrap", path=TOOL_PATH)
        if executable is None:
            raise KlinixError("Bubblewrap (bwrap) is required for isolated package builds")
        self._executable = executable
        self._bootstrap = bootstrap
        programs = (*REQUIRED, *BOOTSTRAP_HOST_TOOLS) if bootstrap else REQUIRED
        missing = [program for program in programs if not os.access(f"/usr/bin/{program}", os.X_OK)]
        if missing:
            raise KlinixError(f"Missing host build tools under /usr/bin: {', '.join(missing)}")

    def _args(self, work: Path, cwd: str, inputs: Path | None, *, bootstrap: bool) -> list[str]:
        args = self._isolation_args(path=TOOL_PATH, home="/work/home")
        for name in TOOLCHAIN:
            if Path(name).is_dir():
                args.extend(("--ro-bind", name, name))
        for name in ("/bin", "/lib", "/lib64"):
            path = Path(name)
            if path.is_symlink() and (link := os.readlink(path)).lstrip("/") == "usr" + name:
                args.extend(("--symlink", link, name))
            elif path.is_dir():
                args.extend(("--ro-bind", name, name))
        args.extend(("--dev", "/dev", "--size", "67108864", "--tmpfs", "/tmp", "--bind", str(work), "/work"))
        if bootstrap:
            args.extend((
                "--bind", str(work / "root"), SYSROOT,
                "--setenv", "PATH", f"{TOOLS}/bin:{TOOL_PATH}",
                "--setenv", "CONFIG_SITE", "/dev/null",
                "--setenv", "CONFIG_SHELL", "/usr/bin/bash",
                "--setenv", "SHELL", "/usr/bin/bash",
                "--setenv", "AWK", "/usr/bin/gawk",
            ))
        if inputs is not None:
            args.extend(("--ro-bind", str(inputs), "/inputs"))
        args.extend(("--remount-ro", "/", "--chdir", cwd, "--"))
        return args

    def _isolation_args(self, *, path: str, home: str) -> list[str]:
        return [
            self._executable, "--unshare-all", "--unshare-user", "--disable-userns",
            "--cap-drop", "ALL", "--die-with-parent", "--new-session", "--clearenv",
            "--setenv", "PATH", path, "--setenv", "LC_ALL", "C",
            "--setenv", "HOME", home, "--setenv", "TMPDIR", "/tmp",
        ]

    def probe(self) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix="klinix-probe-") as temp:
                work = Path(temp)
                (work / "home").mkdir()
                (work / "check.c").write_text("int main(void) { return 0; }\n", encoding="ascii")
                compiler = "gcc"
                if self._bootstrap:
                    (work / "root").mkdir()
                    compiler = "g++"
                for argv in ((f"/usr/bin/{compiler}", "check.c", "-o", "check"), ("/work/check",)):
                    result = self._run(
                        work, "/work", None, argv, subprocess.PIPE, timeout=15, bootstrap=self._bootstrap,
                    )
                    if result.returncode != 0:
                        detail = result.stdout.decode(errors="replace").strip()
                        raise KlinixError(f"Isolated build preflight failed: {detail or result.returncode}")
        except OSError as exc:
            raise KlinixError(f"Cannot prepare isolated build preflight: {exc}") from exc

    def run(
        self, work: Path, cwd: str, inputs: Path, argv: Sequence[str], log: BinaryIO,
        *, timeout: int = 180, bootstrap: bool = False,
    ) -> None:
        self._run_logged(self._args(work, cwd, inputs, bootstrap=bootstrap), argv, log, timeout=timeout)

    def run_in_target(
        self, root: Path, argv: Sequence[str], log: BinaryIO, *, timeout: int,
    ) -> None:
        """Run a verification command with the staged root mounted at /."""
        for directory in (root, root / "dev", root / "tmp"):
            if not stat.S_ISDIR(directory.lstat().st_mode):
                raise KlinixError(f"Target verification requires a real directory at {directory}")
        args = self._isolation_args(path="/usr/bin:/usr/sbin", home="/tmp")
        args.extend((
            "--ro-bind", str(root), "/",
            "--dev", "/dev", "--size", "67108864", "--tmpfs", "/tmp",
            "--chdir", "/tmp", "--",
        ))
        self._run_logged(args, argv, log, timeout=timeout)

    def _run_logged(
        self, args: list[str], argv: Sequence[str], log: BinaryIO, *, timeout: int,
    ) -> None:
        log.write(f"$ {shlex.join(argv)}\n".encode())
        log.flush()
        result = self._execute(args, argv, log, timeout=timeout)
        if result.returncode != 0:
            raise KlinixError(f"{argv[0]} exited with status {result.returncode}")

    def _run(
        self, work: Path, cwd: str, inputs: Path | None, argv: Sequence[str],
        output: BinaryIO | int, *, timeout: int, bootstrap: bool = False,
    ) -> subprocess.CompletedProcess:
        return self._execute(self._args(work, cwd, inputs, bootstrap=bootstrap), argv, output, timeout=timeout)

    def _execute(
        self, args: list[str], argv: Sequence[str], output: BinaryIO | int, *, timeout: int,
    ) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [*args, *argv],
                cwd="/", env={"PATH": TOOL_PATH, "LC_ALL": "C"},
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                timeout=timeout, check=False,
            )
        except OSError as exc:
            raise KlinixError(f"Cannot launch Bubblewrap: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise KlinixError(f"Isolated command timed out after {timeout} seconds: {argv[0]}") from exc
