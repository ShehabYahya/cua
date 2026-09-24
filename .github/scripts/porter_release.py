#!/usr/bin/env python3
"""Bump Porter's canonical version source for automatic releases."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
VERSION_ASSIGNMENT_RE = re.compile(r'(?m)^(?P<prefix>__version__\s*=\s*)["\'](?P<version>[^"\']+)["\'](?P<suffix>\s*)$')


def parse_version(version: str) -> tuple[int, int, int, str | None, str | None]:
    """Parse and validate a semantic version, including prerelease/build data."""
    match = SEMVER_RE.fullmatch(version)
    if not match:
        raise ValueError(f"Invalid semantic version: {version}")
    prerelease = match.group(4)
    if prerelease:
        for identifier in prerelease.split("."):
            if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
                raise ValueError(f"Invalid numeric prerelease identifier: {identifier}")
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        prerelease,
        match.group(5),
    )


def bump_version(version: str, bump: str) -> str:
    major, minor, patch, _, _ = parse_version(version)
    if bump == "major":
        major, minor, patch = major + 1, 0, 0
    elif bump == "minor":
        minor, patch = minor + 1, 0
    elif bump == "patch":
        patch += 1
    else:
        raise ValueError(f"Unsupported bump type: {bump}")
    return f"{major}.{minor}.{patch}"


def update_version_file(path: Path, bump: str) -> str:
    source = path.read_text(encoding="utf-8")
    matches = list(VERSION_ASSIGNMENT_RE.finditer(source))
    if len(matches) != 1:
        raise ValueError(f"Expected one __version__ assignment in {path}; found {len(matches)}")
    current = matches[0].group("version")
    next_version = bump_version(current, bump)
    updated = VERSION_ASSIGNMENT_RE.sub(
        lambda match: f'{match.group("prefix")}"{next_version}"{match.group("suffix")}',
        source,
        count=1,
    )
    path.write_text(updated, encoding="utf-8")
    return next_version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version_file", type=Path)
    parser.add_argument("bump", choices=("major", "minor", "patch"))
    args = parser.parse_args()
    print(update_version_file(args.version_file, args.bump))


if __name__ == "__main__":
    main()
