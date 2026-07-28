import datetime as dt
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

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

    def test_stats_cache_ignores_log_metadata_changes_until_ttl_expires(self):
        first_result = {"generation": 1}
        refreshed_result = {"generation": 2}

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "access.log"
            log_path.write_text("first\n", encoding="utf-8")
            with (
                patch.dict(
                    os.environ, {"BLOG_ADMIN_STATS_CACHE_SECONDS": "60"}
                ),
                patch.object(
                    admin,
                    "_parse_access_log_uncached",
                    side_effect=[first_result, refreshed_result],
                ) as parse_uncached,
                patch.object(
                    admin.time,
                    "monotonic",
                    side_effect=[100.0, 100.0, 100.0, 120.0, 161.0, 161.0, 161.0],
                ),
            ):
                first = parse_access_log(log_path)
                first_key = admin._stats_cache_key
                log_path.write_text(
                    "second version changes both size and mtime\n",
                    encoding="utf-8",
                )
                cached = parse_access_log(log_path)
                cached_key = admin._stats_cache_key
                refreshed = parse_access_log(log_path)
                refreshed_key = admin._stats_cache_key

        self.assertIs(first, first_result)
        self.assertIs(cached, first_result)
        self.assertIs(refreshed, refreshed_result)
        self.assertEqual(first_key, cached_key)
        self.assertNotEqual(first_key, refreshed_key)
        self.assertEqual(parse_uncached.call_count, 2)


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
