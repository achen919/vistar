import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "deploy" / "todo-state-snapshot.py"
SPEC = importlib.util.spec_from_file_location("todo_state_snapshot", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)


class TodoStateSnapshotTests(unittest.TestCase):
    def test_missing_setting_uses_service_default(self):
        self.assertEqual(
            snapshot.configured_state_path("BLOG_ADMIN_HOST=127.0.0.1\n"),
            Path("/var/lib/blog-admin/todos.json"),
        )

    def test_explicit_named_state_variant_is_allowed(self):
        self.assertEqual(
            snapshot.configured_state_path(
                "BLOG_ADMIN_TODO_FILE=/var/lib/blog-admin-personal/todos.json\n"
            ),
            Path("/var/lib/blog-admin-personal/todos.json"),
        )

    def test_duplicate_or_unsafe_setting_fails_closed(self):
        invalid_sources = (
            (
                "BLOG_ADMIN_TODO_FILE=/var/lib/blog-admin/todos.json\n"
                "BLOG_ADMIN_TODO_FILE=/var/lib/blog-admin-alt/todos.json\n"
            ),
            "BLOG_ADMIN_TODO_FILE=\n",
            "  BLOG_ADMIN_TODO_FILE=/var/lib/blog-admin/todos.json\n",
            "BLOG_ADMIN_TODO_FILE =/var/lib/blog-admin/todos.json\n",
            'BLOG_ADMIN_TODO_FILE="/var/lib/blog-admin/todos.json"\n',
            "BLOG_ADMIN_TODO_FILE=/var/lib/blog-admin\\/todos.json\n",
            (
                "BLOG_ADMIN_TODO_FILE=/var/lib/blog-admin/todos.json\n"
                "  BLOG_ADMIN_TODO_FILE=/var/lib/blog-admin-alt/todos.json\n"
            ),
            "BLOG_ADMIN_TODO_FILE=/tmp/todos.json\n",
            "BLOG_ADMIN_TODO_FILE=/var/lib/blog-admin/../todos.json\n",
        )
        for source in invalid_sources:
            with self.subTest(source=source), self.assertRaises(SystemExit):
                snapshot.configured_state_path(source)

    def test_missing_lock_is_created_for_state_directory_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "todos.json"
            directory_stat = os.lstat(temporary)

            with snapshot.locked_state(state_path):
                lock_stat = os.lstat(Path(temporary) / ".todos.json.lock")

            self.assertEqual(lock_stat.st_uid, directory_stat.st_uid)
            self.assertEqual(lock_stat.st_gid, directory_stat.st_gid)
            self.assertEqual(stat.S_IMODE(lock_stat.st_mode), 0o600)

    def test_changed_state_is_preserved_with_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "todos.json"
            state_path.write_bytes(b'{"version":2}\n')
            paths = snapshot.release_paths(root, "release-1")

            snapshot.preserve_failed_state(
                paths,
                state_path,
                state_path.read_bytes(),
                os.lstat(state_path),
            )

            self.assertEqual(paths["failed_state"].read_bytes(), state_path.read_bytes())
            metadata = json.loads(paths["failed_metadata"].read_text(encoding="utf-8"))
            self.assertTrue(metadata["present"])
            self.assertEqual(metadata["statePath"], str(state_path))

    def test_snapshot_checksum_mismatch_fails_closed(self):
        state_path = Path("/var/lib/blog-admin/todos.json")
        metadata = {
            "version": 1,
            "statePath": str(state_path),
            "present": True,
            "uid": os.getuid(),
            "gid": os.getgid(),
            "mode": 0o600,
            "sha256": "0" * 64,
        }
        with self.assertRaises(SystemExit):
            snapshot.validate_metadata(metadata, state_path, b"different")


if __name__ == "__main__":
    unittest.main()
