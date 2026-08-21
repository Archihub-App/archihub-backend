#!/usr/bin/env python3
"""Write ``[project].dependencies`` from pyproject.toml as a requirements file.

WHY NOT ``pip install .``. This is an application, not a library: the container
runs the source tree at ``/app``, so installing the package as well would put a
second copy of ``archihub`` in site-packages and leave which one wins to import
order. Only the dependencies are wanted.

WHY NOT ``requirements.txt``. That file is generated at build time by a script
that merges every plugin's requirements into it and then rewrites the tracked
copy in place - so the manifest that governed an install was a build artefact
nobody could read back. ``pyproject.toml`` is the declaration; this turns it
into something pip accepts, and nothing rewrites it.

The dev extra is deliberately excluded. A runtime import satisfied only by a
dev dependency is a backend that cannot start from a production install, which
is exactly the failure the dependency guard exists to catch.

Usage: export_core_requirements.py <pyproject.toml> <output.txt>
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <pyproject.toml> <output.txt>", file=sys.stderr)
        return 2

    source, destination = Path(argv[1]), Path(argv[2])
    data = tomllib.loads(source.read_text())

    dependencies = data.get("project", {}).get("dependencies")
    if not dependencies:
        print(f"{source}: [project].dependencies is empty or missing", file=sys.stderr)
        return 1

    header = (
        "# GENERATED from pyproject.toml's [project].dependencies.\n"
        "# Do not edit: change pyproject.toml and rebuild.\n"
    )
    destination.write_text(header + "\n".join(dependencies) + "\n")
    print(f"{destination}: {len(dependencies)} core dependencies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
