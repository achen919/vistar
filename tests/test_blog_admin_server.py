import tempfile
import unittest
from pathlib import Path

from tools.blog_admin_server import (
    BlogAdminError,
    post_markdown,
    read_categories,
    replace_category_config,
    validate_slug,
    write_categories,
)


class BlogAdminServerTests(unittest.TestCase):
    def test_replaces_category_config_and_preserves_following_sections(self):
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
                {"name": "技术-agent", "description": "Agent"},
                {"name": "随笔-如何搞钱", "description": "Money"},
            ],
        )

        self.assertIn('name = "技术-agent"', updated)
        self.assertIn('description = "Money"', updated)
        self.assertIn("[[params.socialIcons]]", updated)
        self.assertNotIn("Old description", updated)

    def test_write_categories_updates_toml_and_category_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "content" / "categories").mkdir(parents=True)
            (root / "hugo.toml").write_text("[params]\n\n[menu]\n", encoding="utf-8")

            write_categories(root, [{"name": "技术-llm", "description": "LLM"}])

            self.assertEqual(read_categories(root), [{"name": "技术-llm", "description": "LLM"}])
            self.assertTrue((root / "content" / "categories" / "技术-llm" / "_index.md").exists())

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

    def test_slug_rejects_path_traversal(self):
        with self.assertRaises(BlogAdminError):
            validate_slug("../bad")


if __name__ == "__main__":
    unittest.main()
