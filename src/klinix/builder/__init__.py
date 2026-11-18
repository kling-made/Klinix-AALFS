"""Execute reviewed package builds without mutating an existing target."""

import json
import logging
import tempfile
from dataclasses import asdict
from pathlib import Path

from klinix.builder.files import inventory, new_target, publish, stage_input, unpack_archive
from klinix.builder.recipes import PackageBuild
from klinix.builder.sandbox import Bubblewrap
from klinix.errors import KlinixError

LOGGER = logging.getLogger(__name__)
MANIFEST = Path("var/lib/klinix/build-manifest.json")
ENVIRONMENT = "isolated-host-toolchain"


def preview(build: PackageBuild) -> dict:
    return {
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


def run_build(build: PackageBuild, source_dir: Path, target_path: Path) -> dict:
    target = new_target(target_path)
    sandbox = Bubblewrap()
    sandbox.probe()
    log_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix="klinix-build-", suffix=".log", dir=target.parent, delete=False,
        ) as log, tempfile.TemporaryDirectory(
            prefix=".klinix-build-", dir=target.parent, ignore_cleanup_errors=True,
        ) as temp:
            log_path = Path(log.name)
            workspace = Path(temp)
            inputs = workspace / "inputs"
            work = workspace / "work"
            root = work / "root"
            inputs.mkdir()
            root.mkdir(parents=True)
            (work / "home").mkdir()
            log.write(f"Klinix {build.adapter} / {build.pin.revision}\n".encode())
            for approved in build.inputs:
                filename = approved.filename
                stage_input(source_dir / filename, inputs / filename, approved)
                log.write(f"Verified {filename}\n".encode())
            unpack_archive(inputs / f"{build.source_root}.tar.gz", work / "source", build.source_root)
            for command in build.commands:
                LOGGER.info("%s", command.label)
                sandbox.run(work, f"/work/source/{build.source_root}", inputs, command.argv, log)
            records = inventory(root, build.required_output)
            manifest = {
                **preview(build),
                "schema": 1,
                "files": records,
            }
            manifest_path = root / MANIFEST
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
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
