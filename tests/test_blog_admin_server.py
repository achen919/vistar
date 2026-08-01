import datetime as dt
import io
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from PIL import Image, PngImagePlugin

import tools.blog_admin_server as admin
from tools.blog_admin_server import (
    BlogAdminError,
    ContentSnapshot,
    categories_version,
    category_id,
    hash_password,
    normalize_categories,
    parse_access_log,
    post_markdown,
    read_categories,
    replace_category_config,
    rewrite_post_document,
    site_stats,
    validate_slug,
    verify_password,
    write_categories,
)


def category(identifier: str, name: str, description: str = "") -> dict[str, str]:
    return {"id": identifier, "name": name, "description": description}


@contextmanager
def mocked_content_change(root: Path):
    with (
        patch.object(admin, "ensure_source_repo", return_value=root) as ensure_source,
        patch.object(admin, "ensure_content_clean") as ensure_clean,
        patch.object(
            admin, "finish_content_change", return_value="mock-commit"
        ) as finish_change,
    ):
        yield ensure_source, ensure_clean, finish_change


def write_post_fixture(
    root: Path,
    slug: str,
    *,
    title: str,
    categories: list[str],
    custom_lines: list[str] | None = None,
    content: str = "正文",
) -> Path:
    path = root / "content" / "posts" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    extras = "\n".join(custom_lines or [])
    if extras:
        extras += "\n"
    path.write_text(
        "---\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        "date: 2026-07-20\n"
        "draft: false\n"
        'tags: ["测试"]\n'
        f"categories: {json.dumps(categories, ensure_ascii=False)}\n"
        f"{extras}"
        "---\n\n"
        f"{content}\n",
        encoding="utf-8",
    )
    return path


def post_payload(
    *,
    title: str,
    slug: str,
    categories: list[str],
    content: str = "更新后的正文",
    version: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": title,
        "slug": slug,
        "date": "2026-07-25",
        "draft": False,
        "tags": ["测试", "CRUD"],
        "categories": categories,
        "content": content,
    }
    if version is not None:
        payload["version"] = version
    return payload


def file_tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class CategoryTests(unittest.TestCase):
    def test_replaces_category_config_with_ids_and_preserves_following_sections(self):
        original = """baseURL = 'https://shcxyz.site/'
theme = 'PaperMod'

[[params.blogCategories]]
  name = 'Old'
  description = 'Old description'

[[params.socialIcons]]
  name = 'github'
"""
        updated = replace_category_config(
            original,
            [
                category("cat-agent-001", "技术-agent", "Agent"),
                category("cat-money-001", "随笔-如何搞钱", "Money"),
            ],
        )

        self.assertIn('id = "cat-agent-001"', updated)
        self.assertIn('name = "技术-agent"', updated)
        self.assertIn('description = "Money"', updated)
        self.assertIn("[[params.socialIcons]]", updated)
        self.assertNotIn("Old description", updated)
        self.assertLess(updated.index("技术-agent"), updated.index("随笔-如何搞钱"))

    def test_legacy_category_gets_a_stable_derived_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hugo.toml").write_text(
                """[params]
[[params.blogCategories]]
name = "技术-llm"
description = "LLM"
""",
                encoding="utf-8",
            )

            first = read_categories(root)
            second = read_categories(root)

        expected_id = category_id("技术-llm")
        self.assertEqual(first, [category(expected_id, "技术-llm", "LLM")])
        self.assertEqual(first, second)

    def test_write_categories_updates_toml_pages_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "content" / "categories").mkdir(parents=True)
            (root / "hugo.toml").write_text(
                "[params]\n\n[menu]\n", encoding="utf-8"
            )
            categories = [
                category("cat-llm-0001", "技术-llm", "LLM"),
                category("cat-agent-01", "技术-agent", "Agent"),
            ]

            write_categories(root, categories)

            self.assertEqual(read_categories(root), categories)
            self.assertTrue(
                (root / "content" / "categories" / "技术-llm" / "_index.md").exists()
            )
            text = (root / "hugo.toml").read_text(encoding="utf-8")
            self.assertLess(text.index("技术-llm"), text.index("技术-agent"))

    def test_category_version_changes_for_reorder_or_content_change(self):
        original = [
            category("cat-alpha", "Alpha", "First"),
            category("cat-bravo", "Bravo", "Second"),
        ]
        reordered = list(reversed(original))
        edited = [
            category("cat-alpha", "Alpha", "Changed"),
            category("cat-bravo", "Bravo", "Second"),
        ]

        self.assertEqual(categories_version(original), categories_version(list(original)))
        self.assertNotEqual(categories_version(original), categories_version(reordered))
        self.assertNotEqual(categories_version(original), categories_version(edited))

    def test_supplied_id_survives_a_rename_and_order_is_not_sorted(self):
        normalized = normalize_categories(
            [
                category("cat-stable-id", "Zulu"),
                category("cat-second-id", "Alpha"),
            ]
        )

        self.assertEqual(
            [item["id"] for item in normalized],
            ["cat-stable-id", "cat-second-id"],
        )
        self.assertEqual([item["name"] for item in normalized], ["Zulu", "Alpha"])


class FrontMatterTests(unittest.TestCase):
    def test_post_markdown_uses_json_compatible_front_matter_values(self):
        title, markdown = post_markdown(
            {
                "title": "A: B",
                "date": "2026-07-10",
                "tags": ["Go", "LLM"],
                "categories": ["技术-llm"],
                "content": "正文",
            }
        )

        self.assertEqual(title, "A: B")
        self.assertIn('title: "A: B"', markdown)
        self.assertIn('tags: ["Go", "LLM"]', markdown)
        self.assertIn('categories: ["技术-llm"]', markdown)

    def test_rewrite_preserves_unknown_front_matter_and_comments(self):
        original = """---
title: "旧标题"
date: 2026-07-01
draft: false
tags: ["旧标签"]
categories: ["旧分类"]
description: "这行必须保留"
aliases: ["/legacy/"]
# custom comment
cover:
  image: "cover.png"
  alt: "封面"
---

旧正文
第二行
"""

        updated = rewrite_post_document(
            original,
            {
                "title": "新标题",
                "date": "2026-07-25",
                "draft": True,
                "tags": ["新标签"],
                "categories": ["新分类"],
            },
            content="新正文",
        )

        self.assertIn('title: "新标题"', updated)
        self.assertIn('description: "这行必须保留"', updated)
        self.assertIn('aliases: ["/legacy/"]', updated)
        self.assertIn("# custom comment", updated)
        self.assertIn('cover:\n  image: "cover.png"\n  alt: "封面"', updated)
        self.assertTrue(updated.endswith("\n\n新正文\n"))
        self.assertNotIn("旧正文", updated)

    def test_rewrite_one_field_keeps_existing_body(self):
        original = """---
title: "标题"
categories: ["旧分类"]
custom: keep-me
---

正文第一行
正文第二行
"""
        updated = rewrite_post_document(
            original, {"categories": ["新分类"]}, content=None
        )

        self.assertIn('categories: ["新分类"]', updated)
        self.assertIn("custom: keep-me", updated)
        self.assertTrue(updated.endswith("正文第一行\n正文第二行\n"))

    def test_indented_and_indentless_block_lists_are_read_and_rewritten(self):
        original = """---
title: "Block lists"
tags:
  - "one"
  - two
categories:
- old
- keep
custom: untouched
---

正文
"""

        metadata, _, _ = admin.split_front_matter(original)
        updated = rewrite_post_document(
            original, {"categories": ["new", "keep"]}, content=None
        )

        self.assertEqual(metadata["tags"], ["one", "two"])
        self.assertEqual(metadata["categories"], ["old", "keep"])
        self.assertIn('categories: ["new", "keep"]', updated)
        self.assertIn("custom: untouched", updated)
        self.assertNotRegex(updated, r"(?m)^\s*-\s*[\"']?old[\"']?\s*$")

    def test_unquoted_yaml_flow_lists_are_parsed_as_string_items(self):
        original = """---
title: "Flow lists"
tags: [Hugo, "LLM, Agent", 'single '' quote',]
categories: [技术-agent, 随笔]
---

正文
"""

        metadata, _, _ = admin.split_front_matter(original)

        self.assertEqual(
            metadata["tags"],
            ["Hugo", "LLM, Agent", "single ' quote"],
        )
        self.assertEqual(metadata["categories"], ["技术-agent", "随笔"])

    def test_unsupported_complex_multiline_yaml_fails_closed(self):
        documents = [
            """---
title: "Nested mapping"
categories:
  - name: old
    description: unsupported
---

正文
""",
            """---
title: "Cross-line flow list"
tags: [
  "one",
  "two"
]
---

正文
""",
        ]

        for document in documents:
            with self.subTest(document=document.splitlines()[1]):
                with self.assertRaises(BlogAdminError) as raised:
                    admin.split_front_matter(document)
                self.assertEqual(
                    raised.exception.status, HTTPStatus.UNPROCESSABLE_ENTITY
                )
                self.assertEqual(
                    raised.exception.code, "unsupported_front_matter"
                )

    def test_slug_rejects_path_traversal(self):
        with self.assertRaises(BlogAdminError):
            validate_slug("../bad")

    def test_slug_rejects_unicode_filename_over_255_bytes(self):
        self.assertEqual(validate_slug("界" * 84), "界" * 84)
        with self.assertRaises(BlogAdminError) as raised:
            validate_slug("界" * 85)
        self.assertEqual(raised.exception.code, "invalid_slug")


class PasswordAndSessionTests(unittest.TestCase):
    def setUp(self):
        admin.revoked_sessions.clear()
        admin._revocations_loaded_file = None

    def tearDown(self):
        admin.revoked_sessions.clear()
        admin._revocations_loaded_file = None

    def test_password_hash_verifies_without_storing_plaintext(self):
        password = "correct horse battery staple"
        encoded = hash_password(password, iterations=200_000)

        self.assertTrue(encoded.startswith("pbkdf2_sha256$200000$"))
        self.assertNotIn(password, encoded)
        self.assertTrue(verify_password(password, encoded))
        self.assertFalse(verify_password("wrong password", encoded))
        self.assertFalse(verify_password(password, encoded + "tampered"))
        self.assertFalse(verify_password(password, "not-a-password-hash"))

    def test_password_hash_rejects_short_passwords(self):
        with self.assertRaises(ValueError):
            hash_password("too-short")

    def test_session_signature_expiry_and_revocation(self):
        issued_at = 1_800_000_000
        with patch.dict(
            os.environ,
            {
                "BLOG_ADMIN_SESSION_SECRET": "unit-test-session-secret",
                "BLOG_ADMIN_REVOCATION_FILE": "",
            },
        ):
            with patch.object(admin.time, "time", return_value=issued_at):
                token, payload = admin.create_session("admin")
                self.assertEqual(admin.decode_session(token)["u"], "admin")

                body, signature = token.split(".", 1)
                tampered_body = ("A" if body[0] != "A" else "B") + body[1:]
                self.assertIsNone(admin.decode_session(f"{tampered_body}.{signature}"))

                admin.revoked_sessions[payload["sid"]] = payload["exp"]
                self.assertIsNone(admin.decode_session(token))
                admin.revoked_sessions.clear()

            with patch.object(
                admin.time,
                "time",
                return_value=issued_at + admin.SESSION_TTL_SECONDS + 1,
            ):
                self.assertIsNone(admin.decode_session(token))


class ReadSourceTests(unittest.TestCase):
    def test_existing_local_snapshot_never_triggers_git_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            root.mkdir()
            (root / ".git").mkdir()
            (root / "hugo.toml").write_text("title = 'snapshot'\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"BLOG_ADMIN_SOURCE_DIR": str(root)}),
                patch.object(admin, "ensure_source_repo") as ensure_source,
            ):
                selected = admin.root_for_read()

        self.assertEqual(selected, root.resolve())
        ensure_source.assert_not_called()

    def test_missing_local_snapshot_can_still_be_initialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            with (
                patch.dict(os.environ, {"BLOG_ADMIN_SOURCE_DIR": str(root)}),
                patch.object(
                    admin, "ensure_source_repo", return_value=root.resolve()
                ) as ensure_source,
            ):
                selected = admin.root_for_read()

        self.assertEqual(selected, root.resolve())
        ensure_source.assert_called_once_with()

    def test_forced_source_sync_fetches_once_then_fast_forwards_local_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            root.mkdir()
            (root / ".git").mkdir()
            (root / "hugo.toml").write_text("title = 'snapshot'\n", encoding="utf-8")
            with (
                patch.dict(
                    os.environ,
                    {
                        "BLOG_ADMIN_SOURCE_DIR": str(root),
                        "BLOG_ADMIN_BRANCH": "main",
                        "BLOG_ADMIN_UPDATE_SUBMODULES": "0",
                    },
                ),
                patch.object(admin, "ensure_content_clean") as ensure_clean,
                patch.object(admin, "run") as run,
                patch.object(admin, "_last_source_sync", 0.0),
            ):
                selected = admin.ensure_source_repo(force=True)

        self.assertEqual(selected, root.resolve())
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            commands,
            [
                ["git", "fetch", "origin", "main"],
                ["git", "checkout", "main"],
                ["git", "merge", "--ff-only", "origin/main"],
            ],
        )
        self.assertFalse(any(command[:2] == ["git", "pull"] for command in commands))
        self.assertEqual(ensure_clean.call_count, 2)


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        admin._stats_cache_key = None
        admin._stats_cache_value = None
        admin._stats_cache_expires_at = 0.0

    def tearDown(self):
        admin._stats_cache_key = None
        admin._stats_cache_value = None
        admin._stats_cache_expires_at = 0.0

    @staticmethod
    def log_line(
        stamp: str,
        *,
        ip: str = "192.0.2.10",
        method: str = "GET",
        target: str = "/article/",
        status: int = 200,
        referrer: str = "-",
        user_agent: str = "Mozilla/5.0 TestBrowser",
    ) -> str:
        return (
            f'{ip} - - [{stamp}] "{method} {target} HTTP/1.1" '
            f'{status} 123 "{referrer}" "{user_agent}"'
        )

    def test_access_log_aggregates_pv_uv_and_excludes_non_pageviews(self):
        today = dt.datetime.now(dt.timezone.utc)
        stamp = today.strftime("%d/%b/%Y:%H:%M:%S +0000")
        day = today.date().isoformat()
        lines = [
            self.log_line(stamp),
            self.log_line(stamp),
            self.log_line(
                stamp,
                ip="198.51.100.20",
                target="/other/?utm_source=test",
                status=304,
                referrer="https://example.com/path",
            ),
            self.log_line(stamp, target="/admin/"),
            self.log_line(stamp, target="/assets/app.js"),
            self.log_line(stamp, target="/article/", user_agent="ExampleBot/1.0"),
            self.log_line(stamp, method="POST"),
            self.log_line(stamp, status=404),
            self.log_line(stamp, method="HEAD", target="/head-request/"),
            self.log_line(stamp, status=301, target="/redirect/"),
            self.log_line(
                stamp,
                target="/curl-request/",
                user_agent="curl/8.10.0",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "access.log"
            log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "BLOG_ADMIN_ACCESS_LOG": str(log_path),
                    "BLOG_ADMIN_ANALYTICS_SECRET": "analytics-test-secret",
                    "BLOG_ADMIN_TIMEZONE": "UTC",
                },
            ):
                aggregate = parse_access_log(log_path)
                stats = site_stats(1)

        self.assertEqual(aggregate["totalPv"], 3)
        self.assertEqual(len(aggregate["visitors"]), 2)
        self.assertEqual(aggregate["dailyPv"][day], 3)
        self.assertEqual(len(aggregate["dailyVisitors"][day]), 2)
        self.assertEqual(aggregate["pagePv"][(day, "/article/")], 2)
        self.assertEqual(aggregate["pagePv"][(day, "/other/")], 1)
        self.assertNotIn((day, "/admin/"), aggregate["pagePv"])
        self.assertNotIn((day, "/assets/app.js"), aggregate["pagePv"])
        self.assertNotIn((day, "/head-request/"), aggregate["pagePv"])
        self.assertNotIn((day, "/redirect/"), aggregate["pagePv"])
        self.assertNotIn((day, "/curl-request/"), aggregate["pagePv"])
        self.assertEqual(aggregate["referrers"][(day, "example.com")], 1)
        self.assertEqual(stats["totals"]["pv"], 3)
        self.assertEqual(stats["totals"]["uv"], 2)
        self.assertEqual(stats["totals"]["todayPv"], 3)
        self.assertEqual(stats["totals"]["todayUv"], 2)

    def test_first_stats_load_is_synchronous_and_single_flight(self):
        started = threading.Event()
        release = threading.Event()
        first_result = {"generation": 1}

        def parse(_path):
            started.set()
            self.assertTrue(release.wait(timeout=5))
            return first_result

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "access.log"
            log_path.write_text("first\n", encoding="utf-8")
            with (
                patch.object(
                    admin, "_parse_access_log_uncached", side_effect=parse
                ) as parse_uncached,
                ThreadPoolExecutor(max_workers=6) as executor,
            ):
                futures = [
                    executor.submit(parse_access_log, log_path) for _ in range(6)
                ]
                self.assertTrue(started.wait(timeout=5))
                self.assertFalse(futures[0].done())
                release.set()
                results = [future.result(timeout=5) for future in futures]

        self.assertTrue(all(result is first_result for result in results))
        self.assertEqual(parse_uncached.call_count, 1)

    def test_expired_stats_cache_is_stale_while_single_refresh_runs(self):
        started = threading.Event()
        release = threading.Event()
        stale_result = {"generation": 1}
        refreshed_result = {"generation": 2}

        def refresh(_path):
            started.set()
            self.assertTrue(release.wait(timeout=5))
            return refreshed_result

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "access.log"
            log_path.write_text("first\n", encoding="utf-8")
            admin._stats_cache_key = (
                str(log_path),
                log_path.stat().st_mtime_ns,
                log_path.stat().st_size,
            )
            admin._stats_cache_value = stale_result
            admin._stats_cache_expires_at = 0.0
            with (
                patch.object(
                    admin, "_parse_access_log_uncached", side_effect=refresh
                ) as parse_uncached,
                ThreadPoolExecutor(max_workers=8) as executor,
            ):
                try:
                    results = list(
                        executor.map(
                            lambda _index: parse_access_log(log_path), range(8)
                        )
                    )
                    self.assertTrue(started.wait(timeout=5))
                    self.assertTrue(all(result is stale_result for result in results))
                    self.assertEqual(parse_uncached.call_count, 1)
                finally:
                    release.set()

                self.assertTrue(admin.stats_refresh_lock.acquire(timeout=5))
                admin.stats_refresh_lock.release()
                refreshed = parse_access_log(log_path)

        self.assertIs(refreshed, refreshed_result)
        self.assertEqual(parse_uncached.call_count, 1)

    def test_stats_cache_defaults_to_five_minutes(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(admin.stats_cache_ttl_seconds(), 300)

    def test_failed_background_refresh_keeps_stale_value_with_backoff(self):
        stale_result = {"generation": 1}

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "access.log"
            log_path.write_text("first\n", encoding="utf-8")
            admin._stats_cache_key = (
                str(log_path),
                log_path.stat().st_mtime_ns,
                log_path.stat().st_size,
            )
            admin._stats_cache_value = stale_result
            admin._stats_cache_expires_at = 0.0
            with (
                patch.object(
                    admin,
                    "_parse_access_log_uncached",
                    side_effect=RuntimeError("refresh failed"),
                ) as parse_uncached,
                self.assertLogs("blog_admin", level="ERROR"),
            ):
                self.assertIs(parse_access_log(log_path), stale_result)
                self.assertTrue(admin.stats_refresh_lock.acquire(timeout=5))
                admin.stats_refresh_lock.release()
                self.assertGreater(admin._stats_cache_expires_at, 0.0)
                self.assertIs(parse_access_log(log_path), stale_result)

        self.assertEqual(parse_uncached.call_count, 1)

    def test_refresh_thread_start_failure_releases_single_flight_lock(self):
        stale_result = {"generation": 1}

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "access.log"
            log_path.write_text("first\n", encoding="utf-8")
            admin._stats_cache_key = (
                str(log_path),
                log_path.stat().st_mtime_ns,
                log_path.stat().st_size,
            )
            admin._stats_cache_value = stale_result
            admin._stats_cache_expires_at = 0.0
            with (
                patch.object(
                    admin.threading.Thread,
                    "start",
                    side_effect=RuntimeError("thread unavailable"),
                ),
                self.assertLogs("blog_admin", level="ERROR"),
            ):
                self.assertIs(parse_access_log(log_path), stale_result)

            self.assertTrue(admin.stats_refresh_lock.acquire(blocking=False))
            admin.stats_refresh_lock.release()

    def test_stats_warmup_runs_in_background_and_contains_failures(self):
        started = threading.Event()
        release = threading.Event()

        def warm(_path):
            started.set()
            self.assertTrue(release.wait(timeout=5))
            raise RuntimeError("warmup failed")

        with (
            patch.object(admin, "parse_access_log", side_effect=warm),
            self.assertLogs("blog_admin", level="ERROR") as captured,
        ):
            thread = admin.start_stats_cache_warmup()
            self.assertIsNotNone(thread)
            self.assertTrue(started.wait(timeout=5))
            assert thread is not None
            self.assertTrue(thread.is_alive())
            release.set()
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertTrue(
            any("unable to warm analytics cache" in line for line in captured.output)
        )


class ContentSnapshotTests(unittest.TestCase):
    def test_snapshot_restores_modified_deleted_and_new_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            posts = root / "content" / "posts"
            posts.mkdir(parents=True)
            config = root / "hugo.toml"
            existing = posts / "existing.md"
            config.write_text("title = 'before'\n", encoding="utf-8")
            existing.write_text("before\n", encoding="utf-8")
            snapshot = ContentSnapshot(root, ["hugo.toml", "content/posts"])

            config.write_text("title = 'after'\n", encoding="utf-8")
            existing.unlink()
            (posts / "new.md").write_text("new\n", encoding="utf-8")
            nested = posts / "nested"
            nested.mkdir()
            (nested / "new.md").write_text("nested\n", encoding="utf-8")

            snapshot.restore()

            self.assertEqual(config.read_text(encoding="utf-8"), "title = 'before'\n")
            self.assertEqual(existing.read_text(encoding="utf-8"), "before\n")
            self.assertFalse((posts / "new.md").exists())
            self.assertFalse(nested.exists())

    def test_finish_content_change_rolls_back_before_git_or_hugo_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            posts = root / "content" / "posts"
            posts.mkdir(parents=True)
            existing = posts / "existing.md"
            existing.write_text("before\n", encoding="utf-8")
            snapshot = ContentSnapshot(root, ["content/posts"])
            existing.write_text("after\n", encoding="utf-8")
            (posts / "new.md").write_text("new\n", encoding="utf-8")

            failure = BlogAdminError("mock build failure")
            with (
                patch.object(admin, "prepare_site_build", side_effect=failure),
                patch.object(admin, "commit_changes") as commit_changes,
                patch.object(admin, "unstage_content") as unstage_content,
            ):
                with self.assertRaises(BlogAdminError):
                    admin.finish_content_change(
                        root,
                        snapshot,
                        message="Test rollback",
                        paths=["content/posts"],
                    )

            commit_changes.assert_not_called()
            unstage_content.assert_called_once_with(root, ["content/posts"])
            self.assertEqual(existing.read_text(encoding="utf-8"), "before\n")
            self.assertFalse((posts / "new.md").exists())


class ImageUploadTests(unittest.TestCase):
    @staticmethod
    def png(*, size: tuple[int, int] = (2, 2), metadata: bool = False) -> bytes:
        output = io.BytesIO()
        info = PngImagePlugin.PngInfo()
        if metadata:
            info.add_text("Comment", "must be removed")
        Image.new("RGBA", size, (18, 52, 86, 200)).save(
            output,
            format="PNG",
            pnginfo=info,
        )
        return output.getvalue()

    @staticmethod
    def animated_gif(frames: int) -> bytes:
        images = [
            Image.new("RGB", (2, 2), (index, 20, 30))
            for index in range(frames)
        ]
        output = io.BytesIO()
        images[0].save(
            output,
            format="GIF",
            save_all=True,
            append_images=images[1:],
            duration=40,
            loop=0,
        )
        return output.getvalue()

    def test_image_requires_matching_extension_mime_and_signature(self):
        content = self.png(metadata=True)

        mime_type, extension, normalized = admin.validate_image_upload(
            "封面.PNG", "image/png", content
        )

        self.assertEqual(mime_type, "image/png")
        self.assertEqual(extension, ".png")
        self.assertNotEqual(normalized, content)
        with Image.open(io.BytesIO(normalized)) as image:
            image.load()
            self.assertEqual(image.format, "PNG")
            self.assertNotIn("Comment", image.info)
        for filename, declared_mime, raw, expected_code in [
            ("cover.svg", "image/svg+xml", b"<svg></svg>", "unsupported_image_type"),
            ("cover.avif", "image/avif", b"not-avif", "unsupported_image_type"),
            ("cover.png", "image/jpeg", content, "image_type_mismatch"),
            ("cover.png", "image/png", b"<script>alert(1)</script>", "invalid_image_content"),
            ("../cover.png", "image/png", content, "invalid_image_filename"),
            (" cover.png", "image/png", content, "invalid_image_filename"),
        ]:
            with self.subTest(filename=filename, declared_mime=declared_mime):
                with self.assertRaises(BlogAdminError) as raised:
                    admin.validate_image_upload(filename, declared_mime, raw)
                self.assertEqual(raised.exception.code, expected_code)

    def test_polyglot_like_marker_files_are_rejected(self):
        invalid_files = [
            (
                "fake.jpg",
                "image/jpeg",
                b"\xff\xd8\xff<html><script>alert(1)</script>\xff\xd9",
            ),
            (
                "fake.gif",
                "image/gif",
                b"GIF89a\x01\x00\x01\x00<html><script>alert(1)</script>;",
            ),
        ]
        for filename, mime_type, content in invalid_files:
            with self.subTest(filename=filename):
                with self.assertRaises(BlogAdminError) as raised:
                    admin.validate_image_upload(filename, mime_type, content)
                self.assertEqual(raised.exception.code, "invalid_image_content")

    def test_image_pixel_and_frame_limits_are_enforced(self):
        with patch.object(admin, "MAX_IMAGE_PIXELS", 3):
            with self.assertRaises(BlogAdminError) as pixels:
                admin.validate_image_upload(
                    "large.png",
                    "image/png",
                    self.png(size=(2, 2)),
                )
        self.assertEqual(pixels.exception.code, "image_dimensions_too_large")

        with patch.object(admin, "MAX_IMAGE_FRAMES", 2):
            with self.assertRaises(BlogAdminError) as frames:
                admin.validate_image_upload(
                    "animated.gif",
                    "image/gif",
                    self.animated_gif(3),
                )
        self.assertEqual(frames.exception.code, "image_too_many_frames")

    def test_upload_uses_random_static_path_and_content_publish_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mocked_content_change(root) as (
                    ensure_source,
                    ensure_clean,
                    finish_change,
                ),
                patch.object(
                    admin.secrets, "token_hex", return_value="a" * 36
                ),
            ):
                payload = admin.upload_image(
                    "封面.png", "image/png", self.png()
                )

            ensure_source.assert_called_once_with(force=True)
            ensure_clean.assert_called_once_with(root)
            finish_change.assert_called_once()
            changed_paths = finish_change.call_args.kwargs["paths"]
            self.assertEqual(len(changed_paths), 1)
            self.assertEqual(
                changed_paths[0],
                f"static/{payload['url'].removeprefix('/')}",
            )
            self.assertTrue(changed_paths[0].startswith("static/uploads/"))
            self.assertRegex(
                payload["url"],
                r"^/uploads/\d{4}/\d{2}/a{36}\.png$",
            )
            self.assertEqual(payload["markdown"], f"![封面]({payload['url']})")
            self.assertEqual(payload["mimeType"], "image/png")
            stored = root / "static" / payload["url"].removeprefix("/")
            self.assertEqual(payload["size"], stored.stat().st_size)
            with Image.open(stored) as image:
                image.load()
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (2, 2))

    def test_upload_restores_new_file_when_publish_fails_before_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(admin, "ensure_source_repo", return_value=root),
                patch.object(admin, "ensure_content_clean"),
                patch.object(
                    admin,
                    "finish_content_change",
                    side_effect=BlogAdminError("build failed"),
                ),
            ):
                with self.assertRaises(BlogAdminError):
                    admin.upload_image("cover.png", "image/png", self.png())

            upload_root = root / "static" / "uploads"
            self.assertFalse(
                any(path.is_file() for path in upload_root.rglob("*"))
            )


class TodoTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temporary.name) / "state" / "todos.json"
        self.environment = patch.dict(
            os.environ,
            {
                "BLOG_ADMIN_TODO_FILE": str(self.state_path),
                "BLOG_ADMIN_TIMEZONE": "UTC",
            },
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def test_todo_crud_and_daily_stats(self):
        first = admin.create_todo(
            {"title": "写完文章", "date": "2026-07-28"}
        )
        second = admin.create_todo(
            {"title": "发布文章", "date": "2026-07-28", "completed": True}
        )
        admin.create_todo({"title": "明日任务", "date": "2026-07-29"})

        listed = admin.list_todos("2026-07-28")
        self.assertEqual([item["id"] for item in listed["todos"]], [first["id"], second["id"]])
        self.assertEqual(
            listed["summary"],
            {
                "total": 2,
                "completed": 1,
                "pending": 1,
                "completionRate": 50,
            },
        )

        updated = admin.update_todo(
            first["id"], {"title": "完成文章", "completed": True}
        )
        self.assertEqual(updated["title"], "完成文章")
        self.assertTrue(updated["completed"])
        self.assertIsNotNone(updated["completedAt"])
        stats = admin.todo_stats(2, "2026-07-29")
        self.assertEqual(stats["startDate"], "2026-07-28")
        self.assertEqual(stats["endDate"], "2026-07-29")
        self.assertEqual(stats["totals"]["total"], 3)
        self.assertEqual(stats["totals"]["completed"], 2)
        self.assertEqual(stats["daily"][0]["completionRate"], 100)
        self.assertEqual(stats["daily"][1]["completionRate"], 0)

        deleted = admin.delete_todo(second["id"])
        self.assertEqual(deleted, {"ok": True, "id": second["id"]})
        self.assertEqual(admin.list_todos("2026-07-28")["summary"]["total"], 1)
        self.assertEqual(self.state_path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.state_path.parent.glob("*.tmp")))

    def test_todo_validation_and_not_found_errors(self):
        invalid_payloads = [
            {"title": ""},
            {"title": "x" * 201},
            {"title": "line\nbreak"},
            {"title": "ok", "date": "2026-02-30"},
            {"title": "ok", "unknown": True},
            {"title": "ok", "completed": "yes"},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(BlogAdminError):
                    admin.create_todo(payload)

        missing_id = "todo_" + "a" * 20
        with self.assertRaises(BlogAdminError) as updated:
            admin.update_todo(missing_id, {"completed": True})
        self.assertEqual(updated.exception.status, HTTPStatus.NOT_FOUND)
        self.assertEqual(updated.exception.code, "todo_not_found")
        with self.assertRaises(BlogAdminError) as deleted:
            admin.delete_todo(missing_id)
        self.assertEqual(deleted.exception.status, HTTPStatus.NOT_FOUND)

    def test_corrupt_todo_state_fails_closed(self):
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(
            '{"version":1,"todos":[{"id":"../../bad"}]}\n',
            encoding="utf-8",
        )

        with self.assertRaises(BlogAdminError) as raised:
            admin.list_todos("2026-07-28")

        self.assertEqual(raised.exception.status, HTTPStatus.INTERNAL_SERVER_ERROR)
        self.assertEqual(raised.exception.code, "todo_store_invalid")

    def test_concurrent_creates_do_not_lose_records(self):
        def create(index: int) -> str:
            return admin.create_todo(
                {"title": f"任务 {index}", "date": "2026-07-28"}
            )["id"]

        with ThreadPoolExecutor(max_workers=8) as executor:
            identifiers = list(executor.map(create, range(40)))

        self.assertEqual(len(identifiers), len(set(identifiers)))
        payload = admin.list_todos("2026-07-28")
        self.assertEqual(payload["summary"]["total"], 40)
        with admin.locked_todo_store() as path:
            state = admin.load_todo_state(path)
        self.assertEqual(len(state["todos"]), 40)

    def test_legacy_state_is_read_without_mutation_and_migrates_on_write(self):
        timestamp = "2026-07-28T08:00:00Z"
        legacy = {
            "version": 1,
            "todos": [
                {
                    "id": "todo_" + "a" * 20,
                    "title": "旧任务",
                    "date": "2026-07-28",
                    "completed": True,
                    "createdAt": timestamp,
                    "updatedAt": timestamp,
                    "completedAt": timestamp,
                }
            ],
        }
        self.state_path.parent.mkdir(parents=True)
        original = json.dumps(legacy, ensure_ascii=False) + "\n"
        self.state_path.write_text(original, encoding="utf-8")

        listed = admin.list_todos("2026-07-28")
        self.assertEqual(listed["todos"][0]["title"], "旧任务")
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), original)

        admin.create_todo_plan(
            {
                "title": "每日复盘",
                "repeatType": "daily",
                "startDate": "2026-07-28",
            }
        )
        stored = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["version"], 2)
        self.assertEqual(stored["todos"][0]["id"], legacy["todos"][0]["id"])
        self.assertEqual(len(stored["plans"]), 1)
        self.assertEqual(stored["completions"], [])

    def test_legacy_state_migrates_on_first_ordinary_todo_write(self):
        timestamp = "2026-07-28T08:00:00Z"
        legacy_id = "todo_" + "a" * 20
        legacy = {
            "version": 1,
            "todos": [
                {
                    "id": legacy_id,
                    "title": "旧任务",
                    "date": "2026-07-28",
                    "completed": False,
                    "createdAt": timestamp,
                    "updatedAt": timestamp,
                    "completedAt": None,
                }
            ],
        }
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(
            json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        created = admin.create_todo(
            {"title": "普通新任务", "date": "2026-07-29"}
        )

        stored = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["version"], 2)
        self.assertEqual(
            [item["id"] for item in stored["todos"]], [legacy_id, created["id"]]
        )
        self.assertEqual(stored["plans"], [])
        self.assertEqual(stored["completions"], [])

    def test_recurring_plans_expand_on_demand_and_complete_per_day(self):
        daily = admin.create_todo_plan(
            {
                "title": "晨间阅读",
                "repeatType": "daily",
                "startDate": "2026-07-30",
            }
        )
        weekly = admin.create_todo_plan(
            {
                "title": "周计划",
                "repeatType": "weekly",
                "startDate": "2026-07-27",
                "endDate": "2026-08-02",
                "weekdays": [1, 7],
            }
        )

        thursday = admin.list_todos("2026-07-30")
        self.assertEqual([item["title"] for item in thursday["todos"]], ["晨间阅读"])
        monday = admin.list_todos("2026-07-27")
        self.assertEqual([item["title"] for item in monday["todos"]], ["周计划"])
        sunday = admin.list_todos("2026-08-02")
        self.assertEqual([item["title"] for item in sunday["todos"]], ["晨间阅读", "周计划"])
        self.assertEqual(admin.list_todos("2026-08-03")["summary"]["total"], 1)

        first = admin.update_todo_occurrence(
            daily["id"], "2026-07-30", {"completed": True}
        )
        self.assertTrue(first["completed"])
        self.assertFalse(admin.list_todos("2026-07-31")["todos"][0]["completed"])
        admin.update_todo_occurrence(
            daily["id"], "2026-07-31", {"completed": True}
        )
        admin.update_todo_occurrence(
            daily["id"], "2026-07-30", {"completed": False}
        )
        self.assertFalse(admin.list_todos("2026-07-30")["todos"][0]["completed"])
        self.assertTrue(admin.list_todos("2026-07-31")["todos"][0]["completed"])

        stats = admin.todo_stats(4, "2026-08-02")
        self.assertEqual(stats["totals"]["total"], 5)
        self.assertEqual(stats["totals"]["completed"], 1)
        with admin.locked_todo_store() as path:
            state = admin.load_todo_state(path)
        self.assertEqual(len(state["plans"]), 2)
        self.assertEqual(len(state["completions"]), 1)
        self.assertEqual(weekly["recurrence"]["weekdays"], [1, 7])

    def test_recurring_occurrence_updated_at_uses_timestamp_order(self):
        plan_id = "plan_" + "a" * 20
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "todos": [],
                    "plans": [
                        {
                            "id": plan_id,
                            "title": "精度回归",
                            "repeatType": "daily",
                            "startDate": "2026-08-01",
                            "endDate": None,
                            "weekdays": [1, 2, 3, 4, 5, 6, 7],
                            "createdAt": "2026-08-01T08:00:00Z",
                            "updatedAt": "2026-08-01T08:00:00Z",
                        }
                    ],
                    "completions": [
                        {
                            "planId": plan_id,
                            "date": "2026-08-01",
                            "completedAt": "2026-08-01T08:00:00.1Z",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        occurrence = admin.list_todos("2026-08-01")["todos"][0]

        self.assertEqual(occurrence["updatedAt"], "2026-08-01T08:00:00.1Z")

    def test_todo_stats_expands_snapshot_without_holding_store_lock(self):
        admin.create_todo_plan(
            {
                "title": "每日任务",
                "repeatType": "daily",
                "startDate": "2026-08-01",
            }
        )
        expansion_started = threading.Event()
        allow_expansion = threading.Event()
        original_matches = admin.todo_plan_matches

        def slow_matches(plan, date_value, weekday=None):
            expansion_started.set()
            if not allow_expansion.wait(timeout=2):
                raise AssertionError("Todo statistics expansion timed out")
            return original_matches(plan, date_value, weekday)

        with (
            patch.object(admin, "todo_plan_matches", side_effect=slow_matches),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            stats_future = executor.submit(admin.todo_stats, 1, "2026-08-01")
            self.assertTrue(expansion_started.wait(timeout=1))
            create_future = executor.submit(
                admin.create_todo,
                {"title": "并发写入", "date": "2026-08-01"},
            )
            try:
                created = create_future.result(timeout=1)
            finally:
                allow_expansion.set()
            stats = stats_future.result(timeout=2)

        self.assertTrue(created["id"].startswith("todo_"))
        self.assertEqual(stats["totals"]["total"], 1)

    def test_plan_update_prunes_invalid_completions_and_delete_cascades(self):
        plan = admin.create_todo_plan(
            {
                "title": "训练",
                "repeatType": "weekly",
                "startDate": "2026-07-27",
                "weekdays": [1, 3],
            }
        )
        admin.update_todo_occurrence(
            plan["id"], "2026-07-27", {"completed": True}
        )
        admin.update_todo_occurrence(
            plan["id"], "2026-07-29", {"completed": True}
        )

        updated = admin.update_todo_plan(
            plan["id"], {"title": "力量训练", "weekdays": [3]}
        )
        self.assertEqual(updated["title"], "力量训练")
        with admin.locked_todo_store() as path:
            state = admin.load_todo_state(path)
        self.assertEqual(
            [(item["planId"], item["date"]) for item in state["completions"]],
            [(plan["id"], "2026-07-29")],
        )

        self.assertEqual(
            admin.delete_todo_plan(plan["id"]), {"ok": True, "id": plan["id"]}
        )
        with admin.locked_todo_store() as path:
            state = admin.load_todo_state(path)
        self.assertEqual(state["plans"], [])
        self.assertEqual(state["completions"], [])

    def test_recurring_plan_validation_and_unscheduled_occurrence(self):
        invalid_plans = [
            {
                "title": "坏计划",
                "repeatType": "weekly",
                "startDate": "2026-08-01",
                "weekdays": [],
            },
            {
                "title": "坏计划",
                "repeatType": "weekly",
                "startDate": "2026-08-01",
                "weekdays": [1, 1],
            },
            {
                "title": "坏计划",
                "repeatType": "weekly",
                "startDate": "2026-08-02",
                "endDate": "2026-08-01",
                "weekdays": [7],
            },
            {
                "title": "坏计划",
                "repeatType": "daily",
                "startDate": "2026-08-01",
                "weekdays": [True, 2],
            },
            {
                "title": "坏计划",
                "repeatType": ["daily"],
                "startDate": "2026-08-01",
            },
        ]
        for payload in invalid_plans:
            with self.subTest(payload=payload):
                with self.assertRaises(BlogAdminError):
                    admin.create_todo_plan(payload)

        plan = admin.create_todo_plan(
            {
                "title": "每周一",
                "repeatType": "weekly",
                "startDate": "2026-08-01",
                "weekdays": [1],
            }
        )
        with self.assertRaises(BlogAdminError) as raised:
            admin.update_todo_occurrence(
                plan["id"], "2026-08-02", {"completed": True}
            )
        self.assertEqual(raised.exception.status, HTTPStatus.CONFLICT)
        self.assertEqual(raised.exception.code, "todo_occurrence_not_scheduled")

    def test_recurring_completion_updates_are_idempotent_under_concurrency(self):
        plan = admin.create_todo_plan(
            {
                "title": "每日任务",
                "repeatType": "daily",
                "startDate": "2026-08-01",
            }
        )

        def complete(_: int) -> bool:
            return admin.update_todo_occurrence(
                plan["id"], "2026-08-01", {"completed": True}
            )["completed"]

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(complete, range(24)))
        self.assertTrue(all(results))
        with admin.locked_todo_store() as path:
            state = admin.load_todo_state(path)
        self.assertEqual(len(state["completions"]), 1)

    def test_orphaned_recurring_completion_fails_closed(self):
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "todos": [],
                    "plans": [],
                    "completions": [
                        {
                            "planId": "plan_" + "a" * 20,
                            "date": "2026-08-01",
                            "completedAt": "2026-08-01T08:00:00Z",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaises(BlogAdminError) as raised:
            admin.list_todos("2026-08-01")
        self.assertEqual(raised.exception.status, HTTPStatus.INTERNAL_SERVER_ERROR)
        self.assertEqual(raised.exception.code, "todo_store_invalid")


class CrudConsistencyTests(unittest.TestCase):
    def make_root(self, directory: str) -> tuple[Path, list[dict[str, str]]]:
        root = Path(directory)
        (root / "content" / "posts").mkdir(parents=True)
        (root / "content" / "categories").mkdir(parents=True)
        (root / "hugo.toml").write_text(
            "[params]\n\n[menu]\n", encoding="utf-8"
        )
        categories = [
            category("cat-tech-0001", "旧分类", "技术内容"),
            category("cat-other-001", "其他分类", "其他内容"),
        ]
        write_categories(root, categories)
        return root, categories

    def test_category_rename_updates_multiple_posts_and_preserves_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, categories = self.make_root(tmp)
            first = write_post_fixture(
                root,
                "first",
                title="第一篇",
                categories=["旧分类"],
                custom_lines=[
                    'description: "保留第一篇描述"',
                    'aliases: ["/legacy-first/"]',
                    "# keep first comment",
                ],
            )
            second = write_post_fixture(
                root,
                "second",
                title="第二篇",
                categories=["其他分类", "旧分类"],
                custom_lines=[
                    "featured: true",
                    'cover: "second.png"',
                    "# keep second comment",
                ],
            )
            first.write_text(
                first.read_text(encoding="utf-8")
                .replace('tags: ["测试"]', 'tags:\n  - "测试"')
                .replace('categories: ["旧分类"]', "categories:\n- 旧分类"),
                encoding="utf-8",
            )
            second.write_text(
                second.read_text(encoding="utf-8").replace(
                    'categories: ["其他分类", "旧分类"]',
                    "categories:\n  - 其他分类\n  - 旧分类",
                ),
                encoding="utf-8",
            )
            old_asset = (
                root
                / "content"
                / "categories"
                / "旧分类"
                / "images"
                / "hero.png"
            )
            old_asset.parent.mkdir(parents=True)
            old_asset.write_bytes(b"category-image")
            payload = {
                "version": categories_version(categories),
                "categories": [
                    category("cat-other-001", "其他分类", "其他内容"),
                    category("cat-tech-0001", "新分类", "重命名后的技术内容"),
                ],
            }

            with mocked_content_change(root) as (
                ensure_source,
                ensure_clean,
                finish_change,
            ):
                response = admin.update_categories(payload)

            ensure_source.assert_called_once_with(force=True)
            ensure_clean.assert_called_once_with(root)
            finish_change.assert_called_once()
            self.assertEqual(response["commit"], "mock-commit")
            self.assertEqual(
                [item["id"] for item in response["categories"]],
                ["cat-other-001", "cat-tech-0001"],
            )
            self.assertEqual(
                [item["name"] for item in response["categories"]],
                ["其他分类", "新分类"],
            )
            self.assertEqual(
                admin.get_post(root, "first")["categories"], ["新分类"]
            )
            self.assertEqual(
                admin.get_post(root, "second")["categories"],
                ["其他分类", "新分类"],
            )
            first_text = first.read_text(encoding="utf-8")
            second_text = second.read_text(encoding="utf-8")
            self.assertIn('description: "保留第一篇描述"', first_text)
            self.assertIn('aliases: ["/legacy-first/"]', first_text)
            self.assertIn("# keep first comment", first_text)
            self.assertIn("featured: true", second_text)
            self.assertIn('cover: "second.png"', second_text)
            self.assertIn("# keep second comment", second_text)
            self.assertNotIn("旧分类", first_text)
            self.assertNotIn("旧分类", second_text)
            self.assertFalse(
                (root / "content" / "categories" / "旧分类").exists()
            )
            moved_asset = (
                root
                / "content"
                / "categories"
                / "新分类"
                / "images"
                / "hero.png"
            )
            self.assertEqual(moved_asset.read_bytes(), b"category-image")

    def test_deleting_a_category_in_use_returns_conflict_without_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, categories = self.make_root(tmp)
            write_post_fixture(
                root,
                "used-category",
                title="仍在使用分类",
                categories=["旧分类"],
                custom_lines=['custom: "must-stay"'],
            )
            before = file_tree(root)
            payload = {
                "version": categories_version(categories),
                "categories": [
                    category("cat-other-001", "其他分类", "其他内容")
                ],
            }

            with mocked_content_change(root) as (
                ensure_source,
                ensure_clean,
                finish_change,
            ):
                with self.assertRaises(BlogAdminError) as raised:
                    admin.update_categories(payload)

            self.assertEqual(raised.exception.status, HTTPStatus.CONFLICT)
            self.assertEqual(raised.exception.code, "category_in_use")
            ensure_source.assert_called_once_with(force=True)
            ensure_clean.assert_called_once_with(root)
            finish_change.assert_not_called()
            self.assertEqual(file_tree(root), before)

    def test_deleting_unused_category_removes_its_resource_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, categories = self.make_root(tmp)
            resource = (
                root
                / "content"
                / "categories"
                / "旧分类"
                / "images"
                / "unused.png"
            )
            resource.parent.mkdir(parents=True)
            resource.write_bytes(b"unused-category-image")
            payload = {
                "version": categories_version(categories),
                "categories": [
                    category("cat-other-001", "其他分类", "其他内容")
                ],
            }

            with mocked_content_change(root) as (
                ensure_source,
                ensure_clean,
                finish_change,
            ):
                response = admin.update_categories(payload)

            ensure_source.assert_called_once_with(force=True)
            ensure_clean.assert_called_once_with(root)
            finish_change.assert_called_once()
            self.assertEqual(response["commit"], "mock-commit")
            self.assertFalse(
                (root / "content" / "categories" / "旧分类").exists()
            )
            self.assertTrue(
                (root / "content" / "categories" / "其他分类").exists()
            )

    def test_category_directory_conflict_rolls_back_posts_and_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, categories = self.make_root(tmp)
            post = write_post_fixture(
                root,
                "rename-conflict",
                title="目录冲突",
                categories=["旧分类"],
                custom_lines=['custom: "rollback-me"'],
            )
            post.write_text(
                post.read_text(encoding="utf-8").replace(
                    'categories: ["旧分类"]', "categories:\n- 旧分类"
                ),
                encoding="utf-8",
            )
            old_asset = root / "content" / "categories" / "旧分类" / "old.png"
            old_asset.write_bytes(b"old-resource")
            destination = root / "content" / "categories" / "冲突分类"
            destination.mkdir()
            (destination / "existing.png").write_bytes(b"existing-resource")
            before = file_tree(root)
            payload = {
                "version": categories_version(categories),
                "categories": [
                    category("cat-tech-0001", "冲突分类", "技术内容"),
                    category("cat-other-001", "其他分类", "其他内容"),
                ],
            }

            with mocked_content_change(root) as (
                ensure_source,
                ensure_clean,
                finish_change,
            ):
                with self.assertRaises(BlogAdminError) as raised:
                    admin.update_categories(payload)

            self.assertEqual(raised.exception.status, HTTPStatus.CONFLICT)
            self.assertEqual(
                raised.exception.code, "category_directory_conflict"
            )
            ensure_source.assert_called_once_with(force=True)
            ensure_clean.assert_called_once_with(root)
            finish_change.assert_not_called()
            self.assertEqual(file_tree(root), before)
            self.assertFalse(
                any(
                    path.name.startswith(".admin-category-rename-")
                    for path in (root / "content" / "categories").iterdir()
                )
            )

    def test_update_post_renames_slug_and_preserves_unknown_front_matter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.make_root(tmp)
            old_path = write_post_fixture(
                root,
                "old-slug",
                title="旧标题",
                categories=["旧分类"],
                custom_lines=[
                    'description: "必须保留"',
                    'aliases: ["/old-url/"]',
                    "# editorial note",
                ],
                content="旧正文",
            )
            version = admin.get_post(root, "old-slug")["version"]
            payload = post_payload(
                title="新标题",
                slug="new-slug",
                categories=["旧分类"],
                version=version,
            )

            with mocked_content_change(root) as (
                ensure_source,
                ensure_clean,
                finish_change,
            ):
                response = admin.update_post("old-slug", payload)

            ensure_source.assert_called_once_with(force=True)
            ensure_clean.assert_called_once_with(root)
            finish_change.assert_called_once()
            new_path = root / "content" / "posts" / "new-slug.md"
            self.assertFalse(old_path.exists())
            self.assertTrue(new_path.exists())
            self.assertEqual(response["slug"], "new-slug")
            self.assertEqual(response["commit"], "mock-commit")
            updated = new_path.read_text(encoding="utf-8")
            self.assertIn('description: "必须保留"', updated)
            self.assertIn('aliases: ["/old-url/"]', updated)
            self.assertIn("# editorial note", updated)
            self.assertIn("更新后的正文", updated)
            self.assertNotIn("旧正文", updated)

    def test_update_post_version_conflict_leaves_files_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.make_root(tmp)
            write_post_fixture(
                root,
                "versioned",
                title="有版本的文章",
                categories=["旧分类"],
                custom_lines=['custom: "unchanged"'],
            )
            before = file_tree(root)
            payload = post_payload(
                title="不应写入",
                slug="renamed",
                categories=["旧分类"],
                version="stale-version",
            )

            with mocked_content_change(root) as (
                ensure_source,
                ensure_clean,
                finish_change,
            ):
                with self.assertRaises(BlogAdminError) as raised:
                    admin.update_post("versioned", payload)

            self.assertEqual(raised.exception.status, HTTPStatus.CONFLICT)
            self.assertEqual(raised.exception.code, "version_conflict")
            ensure_source.assert_called_once_with(force=True)
            ensure_clean.assert_called_once_with(root)
            finish_change.assert_not_called()
            self.assertEqual(file_tree(root), before)
            self.assertFalse((root / "content" / "posts" / "renamed.md").exists())

    def test_create_duplicate_slug_returns_conflict_without_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.make_root(tmp)
            write_post_fixture(
                root,
                "duplicate",
                title="已经存在",
                categories=["旧分类"],
                custom_lines=['custom: "original"'],
            )
            before = file_tree(root)
            payload = post_payload(
                title="重复文章",
                slug="duplicate",
                categories=["旧分类"],
            )

            with mocked_content_change(root) as (
                ensure_source,
                ensure_clean,
                finish_change,
            ):
                with self.assertRaises(BlogAdminError) as raised:
                    admin.create_post(payload)

            self.assertEqual(raised.exception.status, HTTPStatus.CONFLICT)
            self.assertEqual(raised.exception.code, "slug_conflict")
            ensure_source.assert_called_once_with(force=True)
            ensure_clean.assert_called_once_with(root)
            finish_change.assert_not_called()
            self.assertEqual(file_tree(root), before)


if __name__ == "__main__":
    unittest.main()
