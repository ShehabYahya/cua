from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path


_TEMP_SUFFIXES = {
    ".part",
    ".crdownload",
    ".tmp",
    ".download",
}


@dataclass(frozen=True)
class FileStamp:
    path: str
    name: str
    inode: int
    size: int
    mtime_ns: int


class DownloadTracker:
    """Track only one explicitly approved download directory.

    The tracker never reads file contents and never mutates the filesystem. It
    exists so a GUI/browser workflow can remember the exact local file created
    by a previous download/rename and hand that path back to a typed upload tool
    without exposing the absolute path to Jev.
    """

    def __init__(
        self,
        root: str | None,
        *,
        max_depth: int = 2,
        max_files: int = 2000,
    ) -> None:
        self.root: Path | None = None
        self.max_depth = max(0, max_depth)
        self.max_files = max(1, max_files)
        if root:
            candidate = Path(root).expanduser()
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                return
            if resolved.is_dir():
                self.root = resolved

    def _acceptable(self, path: Path) -> bool:
        if path.name.startswith("."):
            return False
        if path.suffix.casefold() in _TEMP_SUFFIXES:
            return False
        try:
            return path.is_file() and not path.is_symlink()
        except OSError:
            return False

    def snapshot(self) -> dict[str, FileStamp]:
        root = self.root
        if root is None:
            return {}
        out: dict[str, FileStamp] = {}
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack and len(out) < self.max_files:
            directory, depth = stack.pop()
            try:
                children = list(directory.iterdir())
            except OSError:
                continue
            for path in children:
                if len(out) >= self.max_files:
                    break
                try:
                    if path.is_symlink():
                        continue
                    if path.is_dir():
                        if (
                            depth < self.max_depth
                            and not path.name.startswith(".")
                        ):
                            stack.append((path, depth + 1))
                        continue
                except OSError:
                    continue
                if not self._acceptable(path):
                    continue
                try:
                    stat = path.stat()
                    resolved = path.resolve(strict=True)
                    resolved.relative_to(root)
                except (OSError, RuntimeError, ValueError):
                    continue
                stamp = FileStamp(
                    path=str(resolved),
                    name=resolved.name,
                    inode=int(getattr(stat, "st_ino", 0)),
                    size=int(stat.st_size),
                    mtime_ns=int(stat.st_mtime_ns),
                )
                out[stamp.path] = stamp
        return out

    @staticmethod
    def changed(
        before: dict[str, FileStamp],
        after: dict[str, FileStamp],
    ) -> tuple[FileStamp, ...]:
        before_by_inode = {
            stamp.inode: stamp
            for stamp in before.values()
            if stamp.inode
        }
        changed: list[FileStamp] = []
        for path, stamp in after.items():
            prior = before.get(path)
            if prior is None and stamp.inode:
                prior = before_by_inode.get(stamp.inode)
            if (
                prior is None
                or prior.path != stamp.path
                or prior.size != stamp.size
                or prior.mtime_ns != stamp.mtime_ns
            ):
                changed.append(stamp)
        changed.sort(
            key=lambda stamp: (stamp.mtime_ns, stamp.name.casefold()),
            reverse=True,
        )
        return tuple(changed)

    async def wait_for_changes(
        self,
        before: dict[str, FileStamp],
        *,
        timeout: float = 0.0,
        interval: float = 0.25,
        stop_event=None,
    ) -> tuple[FileStamp, ...]:
        if self.root is None:
            return ()
        deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
        while True:
            if stop_event is not None and stop_event.is_set():
                return ()
            after = self.snapshot()
            changed = self.changed(before, after)
            if changed:
                return changed
            if asyncio.get_running_loop().time() >= deadline:
                return ()
            await asyncio.sleep(interval)

    def validate_recent(
        self,
        paths: tuple[str, ...],
        *,
        limit: int = 6,
    ) -> tuple[str, ...]:
        root = self.root
        if root is None:
            return ()
        valid: list[str] = []
        for raw in paths:
            if len(valid) >= limit:
                break
            raw_path = Path(raw).expanduser()
            try:
                if raw_path.is_symlink():
                    continue
                path = raw_path.resolve(strict=True)
                path.relative_to(root)
            except (OSError, RuntimeError, ValueError):
                continue
            if self._acceptable(path):
                valid.append(str(path))
        return tuple(valid)
