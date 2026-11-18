"""Run a reviewed command in a rootless Bubblewrap sandbox."""

import logging
import os
import platform
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO

from klinix.errors import KlinixError

LOGGER = logging.getLogger(__name__)
TOOL_PATH = "/usr/bin:/bin"
TOOLCHAIN = ("/usr/bin", "/usr/include", "/usr/lib", "/usr/lib64", "/usr/libexec", "/usr/share")
REQUIRED = ("gcc", "ar", "ranlib", "make", "patch", "sed", "cp", "ln", "rm", "install", "env", "cmp", "sh")


class Bubblewrap:
    def __init__(self) -> None:
        if platform.system() != "Linux" or platform.machine() != "x86_64" or os.geteuid() == 0:
            raise KlinixError("Directory package builds require an unprivileged x86-64 Linux user")
        executable = shutil.which("bwrap", path=TOOL_PATH)
        if executable is None:
            raise KlinixError("Bubblewrap (bwrap) is required for isolated package builds")
        self._executable = executable
        missing = [program for program in REQUIRED if not os.access(f"/usr/bin/{program}", os.X_OK)]
        if missing:
            raise KlinixError(f"Missing host build tools under /usr/bin: {', '.join(missing)}")

    def _args(self, work: Path, cwd: str, inputs: Path | None) -> list[str]:
        args = [
            self._executable, "--unshare-all", "--unshare-user", "--disable-userns",
            "--cap-drop", "ALL", "--die-with-parent", "--new-session", "--clearenv",
            "--setenv", "PATH", TOOL_PATH, "--setenv", "LC_ALL", "C",
            "--setenv", "HOME", "/work/home", "--setenv", "TMPDIR", "/tmp",
        ]
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
        if inputs is not None:
            args.extend(("--ro-bind", str(inputs), "/inputs"))
        args.extend(("--remount-ro", "/", "--chdir", cwd, "--"))
        return args

    def probe(self) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix="klinix-probe-") as temp:
                work = Path(temp)
                (work / "home").mkdir()
                (work / "check.c").write_text("int main(void) { return 0; }\n", encoding="ascii")
                for argv in (("/usr/bin/gcc", "check.c", "-o", "check"), ("/work/check",)):
                    result = self._run(work, "/work", None, argv, subprocess.PIPE, timeout=15)
                    if result.returncode != 0:
                        detail = result.stdout.decode(errors="replace").strip()
                        raise KlinixError(f"Isolated build preflight failed: {detail or result.returncode}")
        except OSError as exc:
            raise KlinixError(f"Cannot prepare isolated build preflight: {exc}") from exc

    def run(
        self, work: Path, cwd: str, inputs: Path, argv: Sequence[str], log: BinaryIO,
    ) -> None:
        log.write(f"$ {shlex.join(argv)}\n".encode())
        log.flush()
        result = self._run(work, cwd, inputs, argv, log, timeout=180)
        if result.returncode != 0:
            raise KlinixError(f"{argv[0]} exited with status {result.returncode}")

    def _run(
        self, work: Path, cwd: str, inputs: Path | None, argv: Sequence[str],
        output: BinaryIO | int, *, timeout: int,
    ) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [*self._args(work, cwd, inputs), *argv],
                cwd="/", env={"PATH": TOOL_PATH, "LC_ALL": "C"},
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                timeout=timeout, check=False,
            )
        except OSError as exc:
            raise KlinixError(f"Cannot launch Bubblewrap: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise KlinixError(f"Isolated command timed out after {timeout} seconds: {argv[0]}") from exc
