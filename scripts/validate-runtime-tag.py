#!/usr/bin/env python3
"""Validate a standalone runtime release tag."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import tomllib

VERSION_PATTERN = re.compile(r"[0-9]+[.][0-9]+[.][0-9]+(?:[.]post[0-9]+)?")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("--manifest", type=Path, default=Path("runtime/conda.toml"))
    args = parser.parse_args()

    if VERSION_PATTERN.fullmatch(args.tag) is None:
        raise SystemExit("runtime tags must use X.Y.Z or X.Y.Z.postN, such as 26.5.3.post1")

    manifest = tomllib.loads(args.manifest.read_text())
    ship = manifest["tool"]["conda-ship"]

    if ship.get("runtime-version") != args.tag:
        raise SystemExit(
            f"tag {args.tag!r} does not match runtime-version {ship.get('runtime-version')!r}"
        )


if __name__ == "__main__":
    main()
