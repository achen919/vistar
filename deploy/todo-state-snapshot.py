#!/usr/bin/env python3
"""Create and restore deployment snapshots for the Todo state file.

The service owns its state directory, so rollback data is kept separately in a
root-owned directory.  Snapshot and restore operations use the same advisory
lock as the application to avoid copying a partially-written state file.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, NoReturn


ENV_PATH = Path("/etc/blog-admin.env")
DEFAULT_STATE_PATH = Path("/var/lib/blog-admin/todos.json")
BACKUP_PARENT = Path("/var/backups")
BACKUP_BASE = BACKUP_PARENT / "blog-admin-todos"
RELEASE_PATTERN = re.compile(r"[A-Za-z0-9._-]+")
STATE_DIR_PATTERN = re.compile(r"blog-admin(?:-[A-Za-z0-9._-]+)?")
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def lstat_regular(path: Path, label: str) -> os.stat_result:
    try:
        result = os.lstat(path)
    except FileNotFoundError:
        fail(f"{label} is missing: {path}")
    if stat.S_ISLNK(result.st_mode) or not stat.S_ISREG(result.st_mode):
        fail(f"{label} must be a non-symlink regular file: {path}")
    return result


def lstat_directory(path: Path, label: str) -> os.stat_result:
    try:
        result = os.lstat(path)
    except FileNotFoundError:
        fail(f"{label} is missing: {path}")
    if stat.S_ISLNK(result.st_mode) or not stat.S_ISDIR(result.st_mode):
        fail(f"{label} must be a non-symlink directory: {path}")
    return result


def require_root_owned_directory(path: Path, label: str) -> None:
    result = lstat_directory(path, label)
    if result.st_uid != 0 or stat.S_IMODE(result.st_mode) & 0o022:
        fail(f"{label} must be root-owned and not group/world writable: {path}")


def ensure_root_directory(path: Path, label: str) -> None:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    require_root_owned_directory(path, label)
    os.chmod(path, 0o700)


def configured_state_path(source: str) -> Path:
    matches = re.findall(r"^BLOG_ADMIN_TODO_FILE=([^\r\n]*)$", source, re.MULTILINE)
    if len(matches) > 1:
        fail("Expected at most one BLOG_ADMIN_TODO_FILE setting")
    raw_path = matches[0] if matches else str(DEFAULT_STATE_PATH)
    state_path = Path(raw_path)
    if (
        not state_path.is_absolute()
        or os.path.normpath(raw_path) != raw_path
        or state_path.name != "todos.json"
        or state_path.parent.parent != Path("/var/lib")
        or not STATE_DIR_PATTERN.fullmatch(state_path.parent.name)
    ):
        fail("Todo state path is outside the managed state directory")
    return state_path


def resolve_state_path() -> Path:
    env_stat = lstat_regular(ENV_PATH, "Admin environment")
    if env_stat.st_uid != 0 or stat.S_IMODE(env_stat.st_mode) & 0o022:
        fail("Admin environment must be root-owned and not group/world writable")
    state_path = configured_state_path(ENV_PATH.read_text(encoding="utf-8"))
    require_root_owned_directory(Path("/var"), "/var")
    require_root_owned_directory(Path("/var/lib"), "/var/lib")
    lstat_directory(state_path.parent, "Todo state directory")
    return state_path


def backup_directory(state_path: Path) -> Path:
    require_root_owned_directory(Path("/var"), "/var")
    if not os.path.lexists(BACKUP_PARENT):
        os.mkdir(BACKUP_PARENT, 0o700)
    require_root_owned_directory(BACKUP_PARENT, "Todo backup parent")
    ensure_root_directory(BACKUP_BASE, "Todo backup root")
    destination = BACKUP_BASE / state_path.parent.name
    ensure_root_directory(destination, "Todo backup state directory")
    return destination


def release_paths(root: Path, release_id: str) -> dict[str, Path]:
    if not RELEASE_PATTERN.fullmatch(release_id):
        fail("Unsafe Todo snapshot release identifier")
    return {
        "metadata": root / f"{release_id}.meta.json",
        "state": root / f"{release_id}.state",
        "failed_metadata": root / f"{release_id}.failed-meta.json",
        "failed_state": root / f"{release_id}.failed-state",
    }


def read_regular_bytes(path: Path, label: str) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise
    try:
        result = os.fstat(descriptor)
        if not stat.S_ISREG(result.st_mode):
            fail(f"{label} must be a regular file: {path}")
        if result.st_size > MAX_SNAPSHOT_BYTES:
            fail(f"{label} exceeds the Todo state size limit")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            content = handle.read(MAX_SNAPSHOT_BYTES + 1)
        if len(content) > MAX_SNAPSHOT_BYTES:
            fail(f"{label} exceeds the Todo state size limit")
        return content, result
    finally:
        os.close(descriptor)


def write_exclusive(path: Path, content: bytes, mode: int = 0o600) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, mode)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def write_json_exclusive(path: Path, payload: dict[str, object]) -> None:
    content = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    write_exclusive(path, content)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def locked_state(state_path: Path) -> Iterator[None]:
    state_directory_stat = lstat_directory(
        state_path.parent, "Todo state directory"
    )
    lock_path = state_path.parent / f".{state_path.name}.lock"
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    created = False
    while True:
        try:
            descriptor = os.open(lock_path, os.O_RDWR | no_follow)
            break
        except FileNotFoundError:
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | no_follow,
                    0o600,
                )
            except FileExistsError:
                continue
            created = True
            break
    ready = False
    acquired = False
    try:
        if created:
            os.fchown(
                descriptor,
                state_directory_stat.st_uid,
                state_directory_stat.st_gid,
            )
            os.fchmod(descriptor, 0o600)
        lock_stat = os.fstat(descriptor)
        if not stat.S_ISREG(lock_stat.st_mode):
            fail(f"Todo lock must be a regular file: {lock_path}")
        if (
            lock_stat.st_uid != state_directory_stat.st_uid
            or lock_stat.st_gid != state_directory_stat.st_gid
        ):
            fail("Todo lock ownership does not match its state directory")
        ready = True
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        acquired = True
        yield
    finally:
        try:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
            if created and not ready:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass


def state_snapshot(state_path: Path) -> tuple[bytes | None, os.stat_result | None]:
    if not os.path.lexists(state_path):
        return None, None
    try:
        return read_regular_bytes(state_path, "Todo state")
    except FileNotFoundError:
        return None, None


def metadata_for(
    state_path: Path,
    content: bytes | None,
    result: os.stat_result | None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "version": 1,
        "statePath": str(state_path),
        "present": content is not None,
    }
    if content is not None and result is not None:
        metadata.update(
            {
                "uid": result.st_uid,
                "gid": result.st_gid,
                "mode": stat.S_IMODE(result.st_mode),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return metadata


def create_snapshot(release_id: str) -> None:
    state_path = resolve_state_path()
    root = backup_directory(state_path)
    paths = release_paths(root, release_id)
    for path in paths.values():
        if os.path.lexists(path):
            fail(f"Todo snapshot path already exists: {path}")
    created: list[Path] = []
    try:
        with locked_state(state_path):
            content, result = state_snapshot(state_path)
            if content is not None:
                write_exclusive(paths["state"], content)
                created.append(paths["state"])
            metadata = metadata_for(state_path, content, result)
            write_json_exclusive(paths["metadata"], metadata)
            created.append(paths["metadata"])
            fsync_directory(root)
    except BaseException:
        for path in reversed(created):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise
    print(f"Created Todo rollback snapshot for {release_id}")


def load_metadata(path: Path) -> dict[str, object]:
    content, _ = read_regular_bytes(path, "Todo snapshot metadata")
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"Todo snapshot metadata is invalid: {exc}")
    if not isinstance(payload, dict):
        fail("Todo snapshot metadata must be an object")
    return payload


def validate_metadata(
    metadata: dict[str, object], state_path: Path, snapshot: bytes | None
) -> None:
    present = metadata.get("present")
    if (
        metadata.get("version") != 1
        or metadata.get("statePath") != str(state_path)
        or not isinstance(present, bool)
    ):
        fail("Todo snapshot metadata does not match the active state path")
    if present:
        if snapshot is None:
            fail("Todo snapshot data is missing")
        expected_keys = {"version", "statePath", "present", "uid", "gid", "mode", "sha256"}
        if set(metadata) != expected_keys:
            fail("Todo snapshot metadata has an unexpected schema")
        if not all(isinstance(metadata[key], int) for key in ("uid", "gid", "mode")):
            fail("Todo snapshot ownership metadata is invalid")
        mode = metadata["mode"]
        if mode < 0 or mode > 0o777 or mode & 0o022:
            fail("Todo snapshot mode is unsafe")
        if metadata["sha256"] != hashlib.sha256(snapshot).hexdigest():
            fail("Todo snapshot checksum does not match")
    elif set(metadata) != {"version", "statePath", "present"} or snapshot is not None:
        fail("Todo absent snapshot metadata has an unexpected schema")


def preserve_failed_state(
    paths: dict[str, Path],
    state_path: Path,
    content: bytes | None,
    result: os.stat_result | None,
) -> None:
    if content is None:
        return
    if os.path.lexists(paths["failed_state"]) or os.path.lexists(paths["failed_metadata"]):
        fail("Todo deployment-window recovery files already exist")
    write_exclusive(paths["failed_state"], content)
    try:
        write_json_exclusive(
            paths["failed_metadata"], metadata_for(state_path, content, result)
        )
        fsync_directory(paths["failed_state"].parent)
    except BaseException:
        paths["failed_state"].unlink(missing_ok=True)
        raise
    print(
        "Todo state changed after the deployment snapshot; the newer state was "
        f"preserved at {paths['failed_state']} before rollback.",
        file=sys.stderr,
    )


def atomic_restore(
    state_path: Path, content: bytes, metadata: dict[str, object]
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=state_path.parent,
        prefix=f".{state_path.name}.restore-",
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, int(metadata["mode"]))
        os.fchown(descriptor, int(metadata["uid"]), int(metadata["gid"]))
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, state_path)
        fsync_directory(state_path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def restore_snapshot(release_id: str) -> None:
    state_path = resolve_state_path()
    root = backup_directory(state_path)
    paths = release_paths(root, release_id)
    metadata = load_metadata(paths["metadata"])
    snapshot: bytes | None = None
    if metadata.get("present") is True:
        snapshot, _ = read_regular_bytes(paths["state"], "Todo snapshot data")
    validate_metadata(metadata, state_path, snapshot)

    with locked_state(state_path):
        current, current_stat = state_snapshot(state_path)
        if current != snapshot:
            preserve_failed_state(paths, state_path, current, current_stat)
        if snapshot is None:
            if os.path.lexists(state_path):
                current_stat = os.lstat(state_path)
                if stat.S_ISLNK(current_stat.st_mode) or not stat.S_ISREG(
                    current_stat.st_mode
                ):
                    fail("Refusing to remove an unsafe Todo state during rollback")
                state_path.unlink()
                fsync_directory(state_path.parent)
        else:
            atomic_restore(state_path, snapshot, metadata)
    print(f"Restored Todo rollback snapshot for {release_id}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("backup", "restore"))
    parser.add_argument("release_id")
    return parser.parse_args()


def main() -> None:
    if os.geteuid() != 0:
        fail("Todo deployment snapshots must run as root")
    args = parse_args()
    if args.action == "backup":
        create_snapshot(args.release_id)
    else:
        restore_snapshot(args.release_id)


if __name__ == "__main__":
    main()
