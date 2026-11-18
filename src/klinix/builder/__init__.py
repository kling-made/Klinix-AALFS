"""Execute reviewed package builds without mutating an existing target."""

import json
import logging
import os
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import BinaryIO

from klinix.builder.files import inventory, new_target, publish, stage_input, unpack_archive
from klinix.builder.recipes import SYSROOT, TARGET_TRIPLET, PackageBuild
from klinix.builder.sandbox import Bubblewrap
from klinix.errors import KlinixError

LOGGER = logging.getLogger(__name__)
MANIFEST = Path("var/lib/klinix/build-manifest.json")
ENVIRONMENT = "isolated-host-toolchain"


def build_order(build: PackageBuild) -> tuple[PackageBuild, ...]:
    """Schedule reviewed prerequisites before the stage that consumes them."""
    ordered = {}

    def visit(stage: PackageBuild) -> None:
        if stage.recipe in ordered:
            return
        for prerequisite in stage.prerequisites:
            visit(prerequisite)
        ordered[stage.recipe] = stage

    visit(build)
    return tuple(ordered.values())


def preview(build: PackageBuild) -> dict:
    output = {
        "book": asdict(build.pin),
        "recipe": build.recipe,
        "package": asdict(build.package),
        "adapter": build.adapter,
        "environment": ENVIRONMENT,
        "inputs": [
            {"filename": item.filename, "url": item.artifact.url, "sha256": item.sha256}
            for item in build.inputs
        ],
        "commands": [asdict(command) for command in build.commands],
    }
    if build.bootstrap:
        output.update(sysroot=SYSROOT, target_triplet=TARGET_TRIPLET, source_tree=build.source_root)
    if build.prerequisites:
        output["prerequisites"] = [preview(stage) for stage in build.prerequisites]
    return output


def _run_stage(build: PackageBuild, sandbox: Bubblewrap, work: Path, inputs: Path, log: BinaryIO) -> None:
    sources = work / "source"
    source = sources / build.source_root
    log.write(f"Klinix {build.adapter} / {build.pin.revision}\n".encode())
    for approved in build.inputs:
        filename = approved.filename
        if approved.artifact.kind == "source":
            top = filename.removesuffix(".tar.gz").removesuffix(".tar.xz")
            unpack_archive(inputs / filename, sources, top)
            if approved.unpack_as is not None:
                (sources / top).rename(source / approved.unpack_as)
    for command in build.commands:
        LOGGER.info("%s", command.label)
        cwd = (Path("/work/source") / build.source_root / command.cwd).as_posix()
        sandbox.run(work, cwd, inputs, command.argv, log, timeout=command.timeout, bootstrap=build.bootstrap)


def run_build(build: PackageBuild, source_dir: Path, target_path: Path) -> dict:
    target = new_target(target_path)
    sandbox = Bubblewrap(bootstrap=build.bootstrap)
    sandbox.probe()
    stages = build_order(build)
    log_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix="klinix-build-", suffix=".log", dir=target.parent, delete=False,
        ) as log, tempfile.TemporaryDirectory(
            prefix=".klinix-build-", dir=target.parent, ignore_cleanup_errors=True,
        ) as temp:
            log_path = Path(log.name)
            LOGGER.info("Build log at %s", log_path)
            workspace = Path(temp)
            work = workspace / "work"
            root = work / "root"
            root.mkdir(parents=True)
            (work / "home").mkdir()
            input_dirs = tuple(workspace / f"inputs-{index}" for index in range(len(stages)))
            verified = {}
            for stage, inputs in zip(stages, input_dirs):
                inputs.mkdir()
                for approved in stage.inputs:
                    filename = approved.filename
                    destination = inputs / filename
                    if filename in verified:
                        original, copy = verified[filename]
                        if approved.artifact != original.artifact or approved.sha256 != original.sha256:
                            raise KlinixError(f"Conflicting approvals for source {filename}")
                        os.link(copy, destination)
                    else:
                        stage_input(source_dir / filename, destination, approved)
                        verified[filename] = approved, destination
                        log.write(f"Verified {filename}\n".encode())
            for stage, inputs in zip(stages, input_dirs):
                _run_stage(stage, sandbox, work, inputs, log)
                shutil.rmtree(work / "source")
                shutil.rmtree(inputs)
            required = tuple(path for stage in stages for path in stage.required_output)
            directories = tuple(name for stage in stages for name in stage.root_directories)
            symlinks = tuple(name for stage in stages for name in stage.root_symlinks)
            records = inventory(root, required, bootstrap=build.bootstrap, directories=directories, symlinks=symlinks)
            manifest = {
                **preview(build),
                "schema": 1,
                "files": records,
            }
            manifest_path = root / MANIFEST
            for parent in (root / "var", root / "var/lib"):
                if parent.is_symlink():
                    raise KlinixError(f"Manifest parent must be a real directory: {parent.relative_to(root)}")
                parent.mkdir(exist_ok=True)
            manifest_path.parent.mkdir()
            with manifest_path.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(manifest, indent=2) + "\n")
            log.flush()
            publish(root, target)
    except (OSError, KlinixError) as exc:
        detail = f"{exc}. Build log: {log_path}" if log_path else str(exc)
        raise KlinixError(detail) from exc
    except KeyboardInterrupt as exc:
        detail = f"Build interrupted. Build log: {log_path}" if log_path else "Build interrupted"
        raise KeyboardInterrupt(detail) from exc
    return {"target": str(target), "manifest": str(target / MANIFEST),
            "log": str(log_path), "files": len(records), "book": asdict(build.pin),
            "recipe": build.recipe, "environment": ENVIRONMENT}
