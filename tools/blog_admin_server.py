#!/usr/bin/env python3
"""Private blog-admin API for the Hugo site.

The service is intentionally dependency-free. Nginx is expected to provide
Basic Auth and reverse proxy only /admin/api/ to this localhost process.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tomllib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BRANCH = "main"
REQUEST_LIMIT_BYTES = 2 * 1024 * 1024
CATEGORY_HEADER = "[[params.blogCategories]]"
CONFIG_SECTION_RE = re.compile(r"^\s*\[")
publish_lock = threading.Lock()


class BlogAdminError(Exception):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def source_dir() -> Path:
    return Path(env("BLOG_ADMIN_SOURCE_DIR", str(REPO_ROOT))).resolve()


def public_dir() -> Path | None:
    value = os.environ.get("BLOG_ADMIN_PUBLIC_DIR")
    return Path(value).resolve() if value else None


def branch_name() -> str:
    return env("BLOG_ADMIN_BRANCH", DEFAULT_BRANCH)


def git_env() -> dict[str, str]:
    proc_env = os.environ.copy()
    ssh_key = os.environ.get("BLOG_ADMIN_SSH_KEY")
    if ssh_key:
        proc_env["GIT_SSH_COMMAND"] = (
            f"ssh -i {ssh_key} -o IdentitiesOnly=yes "
            "-o StrictHostKeyChecking=accept-new"
        )
    return proc_env


def run(
    args: list[str],
    cwd: Path,
    timeout: int = 120,
    proc_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=str(cwd),
        env=proc_env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise BlogAdminError(
            f"Command failed: {' '.join(args)}\n{detail}",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )
    return result


def ensure_source_repo() -> Path:
    target = source_dir()
    repo_url = os.environ.get("BLOG_ADMIN_REPO_URL")
    branch = branch_name()
    proc_env = git_env()

    if (target / ".git").exists():
        run(["git", "fetch", "origin", branch], target, proc_env=proc_env)
        run(["git", "checkout", branch], target, proc_env=proc_env)
        run(["git", "pull", "--ff-only", "origin", branch], target, proc_env=proc_env)
    elif repo_url:
        target.parent.mkdir(parents=True, exist_ok=True)
        run(
            ["git", "clone", "--branch", branch, "--recurse-submodules", repo_url, str(target)],
            target.parent,
            timeout=300,
            proc_env=proc_env,
        )
    else:
        raise BlogAdminError(
            "BLOG_ADMIN_SOURCE_DIR is not a git repo and BLOG_ADMIN_REPO_URL is not set.",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    run(["git", "submodule", "update", "--init", "--recursive"], target, timeout=300, proc_env=proc_env)
    return target


def read_categories(root: Path) -> list[dict[str, str]]:
    config = root / "hugo.toml"
    if not config.exists():
        return []
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    categories = data.get("params", {}).get("blogCategories", [])
    result: list[dict[str, str]] = []
    for item in categories:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        result.append(
            {
                "name": name,
                "description": str(item.get("description", "")).strip(),
            }
        )
    return result


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def category_block(categories: list[dict[str, str]]) -> str:
    chunks: list[str] = []
    for category in categories:
        chunks.append(CATEGORY_HEADER)
        chunks.append(f"  name = {toml_string(category['name'])}")
        chunks.append(f"  description = {toml_string(category.get('description', ''))}")
        chunks.append("")
    return "\n".join(chunks).rstrip() + "\n\n"


def replace_category_config(text: str, categories: list[dict[str, str]]) -> str:
    lines = text.splitlines(keepends=True)
    start = next((idx for idx, line in enumerate(lines) if line.strip() == CATEGORY_HEADER), None)

    block = category_block(categories)
    if start is None:
        insert = next(
            (
                idx
                for idx, line in enumerate(lines)
                if line.strip() in {"[[params.socialIcons]]", "[menu]"}
            ),
            len(lines),
        )
        prefix = "" if insert == 0 or lines[insert - 1].endswith("\n\n") else "\n"
        lines[insert:insert] = [prefix + block]
        return "".join(lines)

    end = start
    while end < len(lines):
        stripped = lines[end].strip()
        if end > start and CONFIG_SECTION_RE.match(stripped) and stripped != CATEGORY_HEADER:
            break
        end += 1

    while start > 0 and not lines[start - 1].strip():
        start -= 1

    return "".join(lines[:start]) + block + "".join(lines[end:])


def validate_category_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise BlogAdminError("Category name is required.")
    if any(part in cleaned for part in ["/", "\\", "\x00"]) or cleaned in {".", ".."}:
        raise BlogAdminError(f"Invalid category name: {cleaned}")
    return cleaned


def normalize_categories(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        raise BlogAdminError("Categories must be a list.")
    seen: set[str] = set()
    categories: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise BlogAdminError("Each category must be an object.")
        name = validate_category_name(str(item.get("name", "")))
        if name in seen:
            continue
        seen.add(name)
        categories.append(
            {
                "name": name,
                "description": str(item.get("description", "")).strip(),
            }
        )
    if not categories:
        raise BlogAdminError("At least one category is required.")
    return categories


def write_category_files(root: Path, categories: list[dict[str, str]]) -> None:
    base = root / "content" / "categories"
    base.mkdir(parents=True, exist_ok=True)
    (base / "_index.md").write_text(
        "---\ntitle: '分类'\ndescription: '按写作主题浏览阿辰的博客。'\n---\n",
        encoding="utf-8",
    )
    configured = {category["name"] for category in categories}
    for category in categories:
        name = validate_category_name(category["name"])
        category_dir = (base / name).resolve()
        if base.resolve() not in category_dir.parents:
            raise BlogAdminError(f"Invalid category path: {name}")
        category_dir.mkdir(parents=True, exist_ok=True)
        (category_dir / "_index.md").write_text(
            "---\n"
            f"title: {json.dumps(name, ensure_ascii=False)}\n"
            f"description: {json.dumps(category.get('description', ''), ensure_ascii=False)}\n"
            "---\n",
            encoding="utf-8",
        )

    for child in base.iterdir():
        if not child.is_dir() or child.name in configured:
            continue
        files = [entry for entry in child.iterdir()]
        if len(files) == 1 and files[0].name == "_index.md":
            files[0].unlink()
            child.rmdir()


def write_categories(root: Path, categories: list[dict[str, str]]) -> None:
    config = root / "hugo.toml"
    text = config.read_text(encoding="utf-8")
    config.write_text(replace_category_config(text, categories), encoding="utf-8")
    write_category_files(root, categories)


def validate_slug(slug: str) -> str:
    cleaned = slug.strip().removesuffix(".md")
    if not cleaned:
        raise BlogAdminError("Slug is required.")
    if any(part in cleaned for part in ["/", "\\", "\x00"]) or cleaned in {".", ".."}:
        raise BlogAdminError("Slug cannot contain path separators.")
    if cleaned.startswith("."):
        raise BlogAdminError("Slug cannot start with a dot.")
    return cleaned


def yaml_value(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def yaml_list(values: list[str]) -> str:
    return "[" + ", ".join(yaml_value(value) for value in values) + "]"


def normalize_string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise BlogAdminError(f"{field} must be a list.")
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def post_markdown(payload: dict[str, Any]) -> tuple[str, str]:
    title = str(payload.get("title", "")).strip()
    if not title:
        raise BlogAdminError("Title is required.")
    content = str(payload.get("content", "")).strip()
    if not content:
        raise BlogAdminError("Markdown content is required.")
    date = str(payload.get("date", "")).strip() or dt.date.today().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise BlogAdminError("Date must use YYYY-MM-DD.")
    tags = normalize_string_list(payload.get("tags"), "tags")
    categories = normalize_string_list(payload.get("categories"), "categories")
    if not categories:
        raise BlogAdminError("Select at least one category.")
    draft = bool(payload.get("draft", False))

    body = (
        "---\n"
        f"title: {yaml_value(title)}\n"
        f"date: {date}\n"
        f"draft: {'true' if draft else 'false'}\n"
        f"tags: {yaml_list(tags)}\n"
        f"categories: {yaml_list(categories)}\n"
        "---\n\n"
        f"{content}\n"
    )
    return title, body


def write_post(root: Path, slug: str, markdown: str, overwrite: bool) -> Path:
    posts_dir = root / "content" / "posts"
    posts_dir.mkdir(parents=True, exist_ok=True)
    post_path = (posts_dir / f"{validate_slug(slug)}.md").resolve()
    if posts_dir.resolve() not in post_path.parents:
        raise BlogAdminError("Invalid post path.")
    if post_path.exists() and not overwrite:
        raise BlogAdminError("A post with this slug already exists.", HTTPStatus.CONFLICT)
    post_path.write_text(markdown, encoding="utf-8")
    return post_path


def validate_site(root: Path) -> None:
    destination = Path(tempfile.mkdtemp(prefix="blog-admin-build."))
    try:
        run(["hugo", "--gc", "--minify", "--destination", str(destination)], root, timeout=300)
    finally:
        shutil.rmtree(destination, ignore_errors=True)


def build_live_site(root: Path) -> None:
    target = public_dir()
    if target is None:
        return
    target.mkdir(parents=True, exist_ok=True)
    run(["hugo", "--gc", "--minify", "--destination", str(target)], root, timeout=300)


def commit_and_push(root: Path, title: str) -> str:
    proc_env = git_env()
    run(["git", "add", "hugo.toml", "content/categories", "content/posts"], root, proc_env=proc_env)
    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=str(root),
        env=proc_env,
        check=False,
    )
    if diff.returncode == 0:
        return "No content changes to commit."
    run(
        [
            "git",
            "-c",
            "user.name=Blog Admin",
            "-c",
            "user.email=blog-admin@shcxyz.site",
            "commit",
            "-m",
            f"Publish blog post: {title}",
        ],
        root,
        proc_env=proc_env,
    )
    run(["git", "push", "origin", branch_name()], root, timeout=300, proc_env=proc_env)
    commit = run(["git", "rev-parse", "--short", "HEAD"], root, proc_env=proc_env).stdout.strip()
    return commit


def publish(payload: dict[str, Any]) -> dict[str, Any]:
    with publish_lock:
        root = ensure_source_repo()
        categories = normalize_categories(payload.get("categoryCatalog"))
        selected = normalize_string_list(payload.get("categories"), "categories")
        catalog_names = {category["name"] for category in categories}
        missing = [category for category in selected if category not in catalog_names]
        if missing:
            raise BlogAdminError(f"Selected category is not in the catalog: {', '.join(missing)}")

        title, markdown = post_markdown(payload)
        write_categories(root, categories)
        post_path = write_post(
            root,
            str(payload.get("slug", "")),
            markdown,
            bool(payload.get("overwrite", False)),
        )
        validate_site(root)
        commit = commit_and_push(root, title)
        build_live_site(root)
        return {
            "ok": True,
            "commit": commit,
            "post": str(post_path.relative_to(root)),
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "BlogAdmin/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > REQUEST_LIMIT_BYTES:
            raise BlogAdminError("Request body is too large.", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise BlogAdminError(f"Invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise BlogAdminError("JSON payload must be an object.")
        return payload

    def do_GET(self) -> None:
        try:
            if self.path == "/health":
                self.send_json(HTTPStatus.OK, {"ok": True})
            elif self.path == "/api/categories":
                root = source_dir() if (source_dir() / "hugo.toml").exists() else REPO_ROOT
                self.send_json(HTTPStatus.OK, {"categories": read_categories(root)})
            else:
                self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})
        except BlogAdminError as exc:
            self.send_json(exc.status, {"ok": False, "error": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive server boundary
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:
        try:
            if self.path != "/api/publish":
                self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})
                return
            self.send_json(HTTPStatus.OK, publish(self.read_json()))
        except BlogAdminError as exc:
            self.send_json(exc.status, {"ok": False, "error": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive server boundary
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})


def main() -> None:
    host = env("BLOG_ADMIN_HOST", "127.0.0.1")
    port = int(env("BLOG_ADMIN_PORT", "18080"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Blog admin API listening on http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
