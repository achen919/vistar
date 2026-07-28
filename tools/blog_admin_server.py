#!/usr/bin/env python3
"""Private management API for the Hugo site.

The service intentionally uses only the Python standard library. Nginx serves
the static admin shell and proxies ``/admin/api/`` to this localhost process.
Authentication is enforced again in this process so a reverse-proxy mistake
does not expose content publishing.
"""

from __future__ import annotations

import ast
import base64
import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections import Counter, defaultdict
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BRANCH = "main"
REQUEST_LIMIT_BYTES = 2 * 1024 * 1024
LOGIN_REQUEST_LIMIT_BYTES = 16 * 1024
SESSION_COOKIE = "blog_admin_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5
SOURCE_SYNC_INTERVAL_SECONDS = 30
MAX_CATEGORY_COUNT = 100
MAX_STATS_DAYS = 365
CATEGORY_HEADER = "[[params.blogCategories]]"
CONFIG_SECTION_RE = re.compile(r"^\s*\[")
FRONT_MATTER_FIELD_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*:")
ACCESS_LOG_RE = re.compile(
    r'^(?P<ip>\S+)\s+\S+\s+\S+\s+\[(?P<time>[^\]]+)\]\s+'
    r'"(?P<method>\S+)\s+(?P<target>\S+)\s+[^"]+"\s+'
    r'(?P<status>\d{3})\s+\S+\s+"(?P<referrer>[^"]*)"\s+"(?P<ua>[^"]*)"'
)
BOT_RE = re.compile(
    r"bot|spider|crawler|slurp|bingpreview|headless|lighthouse|monitor|uptime|"
    r"curl|wget|python-requests|go-http-client",
    re.IGNORECASE,
)
STATIC_SUFFIXES = {
    ".avif",
    ".css",
    ".eot",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".map",
    ".png",
    ".rss",
    ".svg",
    ".ttf",
    ".txt",
    ".webmanifest",
    ".webp",
    ".woff",
    ".woff2",
    ".xml",
}

logger = logging.getLogger("blog_admin")
publish_lock = threading.RLock()
source_sync_lock = threading.RLock()
login_lock = threading.RLock()
audit_lock = threading.Lock()
stats_lock = threading.Lock()
stats_refresh_lock = threading.Lock()
login_failures: dict[str, list[float]] = defaultdict(list)
revoked_sessions: dict[str, int] = {}
_revocations_loaded_file: str | None = None
ephemeral_session_secret = secrets.token_bytes(48)
_last_source_sync = 0.0
_stats_cache_key: tuple[str, int, int] | None = None
_stats_cache_value: dict[str, Any] | None = None
_stats_cache_expires_at = 0.0


class BlogAdminError(Exception):
    def __init__(
        self,
        message: str,
        status: HTTPStatus = HTTPStatus.BAD_REQUEST,
        *,
        code: str = "bad_request",
        detail: str | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.detail = detail


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def source_dir() -> Path:
    return Path(env("BLOG_ADMIN_SOURCE_DIR", str(REPO_ROOT))).resolve()


def public_dir() -> Path | None:
    value = os.environ.get("BLOG_ADMIN_PUBLIC_DIR")
    return Path(value).resolve() if value else None


def branch_name() -> str:
    return env("BLOG_ADMIN_BRANCH", DEFAULT_BRANCH)


def should_update_submodules() -> bool:
    return env("BLOG_ADMIN_UPDATE_SUBMODULES", "1") != "0"


def analytics_log_path() -> Path:
    return Path(env("BLOG_ADMIN_ACCESS_LOG", "/www/wwwlogs/blog.log")).resolve()


def timezone() -> ZoneInfo:
    try:
        return ZoneInfo(env("BLOG_ADMIN_TIMEZONE", "Asia/Shanghai"))
    except Exception:
        return ZoneInfo("UTC")


def git_env() -> dict[str, str]:
    proc_env = os.environ.copy()
    ssh_key = os.environ.get("BLOG_ADMIN_SSH_KEY")
    if ssh_key:
        proc_env["GIT_SSH_COMMAND"] = (
            f"ssh -i {shlex.quote(ssh_key)} -o IdentitiesOnly=yes "
            "-o StrictHostKeyChecking=yes"
        )
    return proc_env


def run(
    args: list[str],
    cwd: Path,
    *,
    timeout: int = 120,
    proc_env: dict[str, str] | None = None,
    public_message: str = "站点操作失败，请稍后重试。",
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd),
            env=proc_env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.exception("command execution failed: %s", args)
        raise BlogAdminError(
            public_message,
            HTTPStatus.INTERNAL_SERVER_ERROR,
            code="command_failed",
            detail=str(exc),
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        logger.error("command failed (%s): %s", result.returncode, detail)
        raise BlogAdminError(
            public_message,
            HTTPStatus.INTERNAL_SERVER_ERROR,
            code="command_failed",
            detail=detail,
        )
    return result


def git_status(root: Path) -> str:
    return run(
        ["git", "status", "--porcelain", "--", "hugo.toml", "content"],
        root,
        proc_env=git_env(),
        public_message="无法检查内容仓库状态。",
    ).stdout.strip()


def ensure_content_clean(root: Path) -> None:
    if git_status(root):
        raise BlogAdminError(
            "内容仓库存在未完成的修改，请先在服务器处理后重试。",
            HTTPStatus.CONFLICT,
            code="dirty_source",
        )


def pending_publish_status(root: Path) -> dict[str, Any]:
    """Return local commits that still need to reach the configured remote branch."""
    if not (root / ".git").exists():
        return {
            "pending": False,
            "count": 0,
            "branch": branch_name(),
            "commits": [],
        }

    revision = f"origin/{branch_name()}..HEAD"
    count = int(
        run(
            ["git", "rev-list", "--count", revision],
            root,
            proc_env=git_env(),
            public_message="无法检查待推送提交。",
        ).stdout.strip()
        or "0"
    )
    commits: list[dict[str, str]] = []
    if count:
        output = run(
            [
                "git",
                "log",
                "--format=%h%x09%s",
                "--max-count=10",
                revision,
            ],
            root,
            proc_env=git_env(),
            public_message="无法读取待推送提交。",
        ).stdout
        for line in output.splitlines():
            commit, separator, subject = line.partition("\t")
            if commit:
                commits.append(
                    {
                        "commit": commit,
                        "subject": subject if separator else "",
                    }
                )
    return {
        "pending": count > 0,
        "count": count,
        "branch": branch_name(),
        "commits": commits,
    }


def require_publish_queue_empty(root: Path) -> None:
    status = pending_publish_status(root)
    if status["pending"]:
        raise BlogAdminError(
            "上一次修改已安全提交到服务器，但仍待推送。请先重试发布，勿重复执行内容修改。",
            HTTPStatus.SERVICE_UNAVAILABLE,
            code="publish_pending",
        )


def push_pending_commits(root: Path) -> dict[str, Any]:
    status = pending_publish_status(root)
    if not status["pending"]:
        return status
    try:
        run(
            ["git", "push", "origin", branch_name()],
            root,
            timeout=300,
            proc_env=git_env(),
            public_message="内容已在服务器提交，但暂时无法推送到 GitHub。请使用发布重试功能，勿重复保存或删除。",
        )
    except BlogAdminError as exc:
        raise BlogAdminError(
            "内容已在服务器提交，但暂时无法推送到 GitHub。请使用发布重试功能，勿重复保存或删除。",
            HTTPStatus.SERVICE_UNAVAILABLE,
            code="publish_pending",
            detail=exc.detail,
        ) from exc
    run(
        [
            "git",
            "update-ref",
            f"refs/remotes/origin/{branch_name()}",
            "HEAD",
        ],
        root,
        proc_env=git_env(),
        public_message="内容已推送，但无法更新本地发布状态。",
    )
    return pending_publish_status(root)


def retry_pending_publish() -> dict[str, Any]:
    with publish_lock:
        root = source_dir()
        if not (root / ".git").exists():
            raise BlogAdminError(
                "内容仓库尚未配置。",
                HTTPStatus.INTERNAL_SERVER_ERROR,
                code="source_not_configured",
            )
        ensure_content_clean(root)
        return push_pending_commits(root)


def ensure_source_repo(*, force: bool = False) -> Path:
    global _last_source_sync

    target = source_dir()
    repo_url = os.environ.get("BLOG_ADMIN_REPO_URL")
    branch = branch_name()
    proc_env = git_env()
    with source_sync_lock:
        if (
            not force
            and (target / ".git").exists()
            and time.monotonic() - _last_source_sync < SOURCE_SYNC_INTERVAL_SECONDS
        ):
            return target

        if (target / ".git").exists():
            ensure_content_clean(target)
            run(
                ["git", "fetch", "origin", branch],
                target,
                proc_env=proc_env,
                public_message="无法同步远端内容，请稍后重试。",
            )
            run(
                ["git", "checkout", branch],
                target,
                proc_env=proc_env,
                public_message="无法切换内容分支。",
            )
            run(
                ["git", "pull", "--ff-only", "origin", branch],
                target,
                proc_env=proc_env,
                public_message="远端内容存在冲突，请在服务器处理后重试。",
            )
        elif repo_url:
            target.parent.mkdir(parents=True, exist_ok=True)
            run(
                [
                    "git",
                    "clone",
                    "--branch",
                    branch,
                    "--recurse-submodules",
                    repo_url,
                    str(target),
                ],
                target.parent,
                timeout=300,
                proc_env=proc_env,
                public_message="无法初始化内容仓库。",
            )
        else:
            raise BlogAdminError(
                "内容仓库尚未配置。",
                HTTPStatus.INTERNAL_SERVER_ERROR,
                code="source_not_configured",
            )

        if should_update_submodules():
            run(
                ["git", "submodule", "update", "--init", "--recursive"],
                target,
                timeout=300,
                proc_env=proc_env,
                public_message="无法更新站点主题。",
            )
        ensure_content_clean(target)
        _last_source_sync = time.monotonic()
        return target


def root_for_read() -> Path:
    target = source_dir()
    if os.environ.get("BLOG_ADMIN_SOURCE_DIR") and (target / ".git").exists():
        try:
            return ensure_source_repo()
        except BlogAdminError:
            if (target / "hugo.toml").exists():
                logger.exception("read-side source sync failed; serving the last local snapshot")
                return target
            raise
    if (target / "hugo.toml").exists():
        return target
    return REPO_ROOT


def category_id(name: str) -> str:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    return f"cat-{digest}"


def validate_category_id(value: str, name: str) -> str:
    cleaned = value.strip() or category_id(name)
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", cleaned):
        raise BlogAdminError("分类 ID 无效。", code="invalid_category_id")
    return cleaned


def validate_category_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise BlogAdminError("分类名称不能为空。", code="invalid_category")
    if len(cleaned) > 80:
        raise BlogAdminError("分类名称不能超过 80 个字符。", code="invalid_category")
    if len(cleaned.encode("utf-8")) > 240:
        raise BlogAdminError("分类名称的 UTF-8 文件名过长。", code="invalid_category")
    if (
        any(part in cleaned for part in ["/", "\\", "\x00"])
        or cleaned in {".", ".."}
        or any(ord(char) < 32 for char in cleaned)
    ):
        raise BlogAdminError(f"分类名称无效：{cleaned}", code="invalid_category")
    return cleaned


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
        raw_id = str(item.get("id", "")).strip()
        result.append(
            {
                "id": raw_id if re.fullmatch(r"[A-Za-z0-9_-]{8,64}", raw_id) else category_id(name),
                "name": name,
                "description": str(item.get("description", "")).strip(),
            }
        )
    return result


def normalize_categories(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        raise BlogAdminError("分类数据必须是数组。", code="invalid_categories")
    if not raw or len(raw) > MAX_CATEGORY_COUNT:
        raise BlogAdminError(
            f"分类数量必须在 1 到 {MAX_CATEGORY_COUNT} 之间。",
            code="invalid_categories",
        )

    names: set[str] = set()
    identifiers: set[str] = set()
    categories: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise BlogAdminError("每个分类都必须是对象。", code="invalid_categories")
        name = validate_category_name(str(item.get("name", "")))
        identifier = validate_category_id(str(item.get("id", "")), name)
        description = str(item.get("description", "")).strip()
        if len(description) > 240:
            raise BlogAdminError("分类描述不能超过 240 个字符。", code="invalid_category")
        if name in names:
            raise BlogAdminError(f"分类名称重复：{name}", code="duplicate_category")
        if identifier in identifiers:
            raise BlogAdminError("分类 ID 重复。", code="duplicate_category_id")
        names.add(name)
        identifiers.add(identifier)
        categories.append(
            {"id": identifier, "name": name, "description": description}
        )
    return categories


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def category_block(categories: list[dict[str, str]]) -> str:
    chunks: list[str] = []
    for category in categories:
        chunks.append(CATEGORY_HEADER)
        chunks.append(f"  id = {toml_string(category['id'])}")
        chunks.append(f"  name = {toml_string(category['name'])}")
        chunks.append(
            f"  description = {toml_string(category.get('description', ''))}"
        )
        chunks.append("")
    return "\n".join(chunks).rstrip() + "\n\n"


def replace_category_config(text: str, categories: list[dict[str, str]]) -> str:
    normalized = normalize_categories(categories)
    lines = text.splitlines(keepends=True)
    start = next(
        (idx for idx, line in enumerate(lines) if line.strip() == CATEGORY_HEADER),
        None,
    )
    block = category_block(normalized)
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
        if (
            end > start
            and CONFIG_SECTION_RE.match(stripped)
            and stripped != CATEGORY_HEADER
        ):
            break
        end += 1
    while start > 0 and not lines[start - 1].strip():
        start -= 1
    return "".join(lines[:start]) + block + "".join(lines[end:])


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_category_files(root: Path, categories: list[dict[str, str]]) -> None:
    base = root / "content" / "categories"
    base.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        base / "_index.md",
        "---\ntitle: '分类'\ndescription: '按写作主题浏览阿辰的博客。'\n---\n",
    )
    configured = {category["name"] for category in categories}
    for category in categories:
        name = validate_category_name(category["name"])
        category_dir = (base / name).resolve()
        if base.resolve() not in category_dir.parents:
            raise BlogAdminError("分类路径无效。", code="invalid_category")
        category_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            category_dir / "_index.md",
            "---\n"
            f"title: {json.dumps(name, ensure_ascii=False)}\n"
            f"description: {json.dumps(category.get('description', ''), ensure_ascii=False)}\n"
            "---\n",
        )

    for child in base.iterdir():
        if not child.is_dir() or child.name in configured:
            continue
        files = list(child.iterdir())
        if len(files) == 1 and files[0].name == "_index.md":
            files[0].unlink()
            child.rmdir()


def write_categories(root: Path, categories: list[dict[str, str]]) -> None:
    normalized = normalize_categories(categories)
    config = root / "hugo.toml"
    text = config.read_text(encoding="utf-8")
    atomic_write_text(config, replace_category_config(text, normalized))
    write_category_files(root, normalized)


def categories_version(categories: list[dict[str, str]]) -> str:
    canonical = json.dumps(
        [
            {
                "id": item["id"],
                "name": item["name"],
                "description": item.get("description", ""),
            }
            for item in categories
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def validate_slug(slug: str) -> str:
    cleaned = slug.strip().removesuffix(".md")
    if not cleaned:
        raise BlogAdminError("Slug 不能为空。", code="invalid_slug")
    if len(cleaned) > 160:
        raise BlogAdminError("Slug 不能超过 160 个字符。", code="invalid_slug")
    if len(f"{cleaned}.md".encode("utf-8")) > 255:
        raise BlogAdminError(
            "Slug 的 UTF-8 文件名过长。",
            code="invalid_slug",
        )
    if (
        any(part in cleaned for part in ["/", "\\", "\x00"])
        or cleaned in {".", ".."}
        or cleaned.startswith(".")
        or not re.fullmatch(r"[\w\u3400-\u9fff-]+", cleaned, re.UNICODE)
    ):
        raise BlogAdminError(
            "Slug 只能包含中文、字母、数字、连字符和下划线。",
            code="invalid_slug",
        )
    return cleaned


def yaml_value(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def yaml_list(values: list[str]) -> str:
    return "[" + ", ".join(yaml_value(value) for value in values) + "]"


def normalize_string_list(
    value: Any, field: str, *, max_items: int = 30, max_length: int = 80
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise BlogAdminError(f"{field} 必须是数组。", code="invalid_post")
    if len(value) > max_items:
        raise BlogAdminError(f"{field} 数量过多。", code="invalid_post")
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if len(text) > max_length:
            raise BlogAdminError(f"{field} 中有内容过长。", code="invalid_post")
        if text and text not in result:
            result.append(text)
    return result


def parse_front_matter_value(value: str) -> Any:
    cleaned = value.strip()
    lowered = cleaned.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "~"}:
        return None
    try:
        return ast.literal_eval(cleaned)
    except (SyntaxError, ValueError):
        return cleaned.strip("'\"")


def parse_yaml_flow_string_list(value: str, field: str) -> list[str]:
    """Parse a conservative, single-line YAML flow sequence of string scalars."""

    def unsupported() -> BlogAdminError:
        return BlogAdminError(
            f"{field} 使用了管理端不支持的复杂 YAML 数组。",
            HTTPStatus.UNPROCESSABLE_ENTITY,
            code="unsupported_front_matter",
        )

    def parse_item(raw: str) -> str:
        item = raw.strip()
        if not item:
            raise unsupported()
        if item.startswith("'"):
            if len(item) < 2 or not item.endswith("'"):
                raise unsupported()
            inner = item[1:-1]
            cursor = 0
            output: list[str] = []
            while cursor < len(inner):
                if inner[cursor] == "'":
                    if cursor + 1 >= len(inner) or inner[cursor + 1] != "'":
                        raise unsupported()
                    output.append("'")
                    cursor += 2
                else:
                    output.append(inner[cursor])
                    cursor += 1
            return "".join(output)
        if item.startswith('"'):
            if len(item) < 2 or not item.endswith('"'):
                raise unsupported()
            try:
                parsed = ast.literal_eval(item)
            except (SyntaxError, ValueError):
                raise unsupported()
            if not isinstance(parsed, str):
                raise unsupported()
            return parsed
        if item[0] in "{[&*!|>@`" or re.search(r":(?:\s|[\[\]{},])", item):
            raise unsupported()
        if any(ord(char) < 32 for char in item):
            raise unsupported()
        return item

    cleaned = value.strip()
    if not cleaned.startswith("["):
        raise unsupported()

    items: list[str] = []
    token: list[str] = []
    quote = ""
    escaped = False
    saw_separator = False
    index = 1
    while index < len(cleaned):
        char = cleaned[index]
        if quote == '"':
            token.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quote = ""
            index += 1
            continue
        if quote == "'":
            token.append(char)
            if char == "'":
                if index + 1 < len(cleaned) and cleaned[index + 1] == "'":
                    token.append("'")
                    index += 2
                    continue
                quote = ""
            index += 1
            continue

        if char in {"'", '"'} and not "".join(token).strip():
            quote = char
            token.append(char)
        elif char in "[{":
            raise unsupported()
        elif char == "}":
            raise unsupported()
        elif char == ",":
            if not "".join(token).strip():
                raise unsupported()
            items.append(parse_item("".join(token)))
            token = []
            saw_separator = True
        elif char == "]":
            final = "".join(token).strip()
            if final:
                items.append(parse_item(final))
            elif not saw_separator and items:
                raise unsupported()
            remainder = cleaned[index + 1 :].strip()
            if remainder and not remainder.startswith("#"):
                raise unsupported()
            return items
        elif char == "#" and (not token or token[-1].isspace()):
            raise unsupported()
        else:
            token.append(char)
        index += 1

    raise unsupported()


def split_front_matter(text: str) -> tuple[dict[str, Any], list[str], str]:
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[0].strip() == "+++":
        raise BlogAdminError(
            "管理端暂不支持 TOML front matter，请先转换为 YAML。",
            HTTPStatus.UNPROCESSABLE_ENTITY,
            code="unsupported_front_matter",
        )
    if not lines or lines[0].strip() != "---":
        return {}, [], text
    closing = next(
        (idx for idx, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing is None:
        raise BlogAdminError("文章 front matter 未闭合。", code="invalid_front_matter")
    front_lines = lines[1:closing]
    metadata: dict[str, Any] = {}
    index = 0
    while index < len(front_lines):
        line = front_lines[index]
        match = FRONT_MATTER_FIELD_RE.match(line)
        if not match:
            index += 1
            continue
        key = match.group(1)
        raw_value = line.split(":", 1)[1].strip()
        if key in {"tags", "categories"} and not raw_value:
            values: list[str] = []
            cursor = index + 1
            while cursor < len(front_lines):
                continuation = front_lines[cursor]
                if not continuation.strip():
                    cursor += 1
                    continue
                item = re.match(r"^\s*-\s+(.+?)\s*$", continuation)
                if item:
                    parsed_item = parse_front_matter_value(item.group(1))
                    if isinstance(parsed_item, (dict, list, tuple)):
                        raise BlogAdminError(
                            f"{key} 只能包含字符串。",
                            HTTPStatus.UNPROCESSABLE_ENTITY,
                            code="unsupported_front_matter",
                        )
                    values.append(str(parsed_item))
                    cursor += 1
                    continue
                if not continuation[0].isspace():
                    break
                else:
                    raise BlogAdminError(
                        f"{key} 使用了管理端不支持的多行 YAML 格式。",
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        code="unsupported_front_matter",
                    )
            metadata[key] = values
            index = cursor
            continue
        if key in {"tags", "categories"} and raw_value.startswith("["):
            metadata[key] = parse_yaml_flow_string_list(raw_value, key)
            index += 1
            continue
        metadata[key] = parse_front_matter_value(raw_value)
        index += 1
    body = "\n".join(lines[closing + 1 :]).lstrip("\n")
    return metadata, front_lines, body


def serialized_front_matter_value(key: str, value: Any) -> str:
    if key in {"tags", "categories"}:
        return yaml_list([str(item) for item in value])
    if key == "draft":
        return "true" if value else "false"
    return yaml_value(str(value)) if key == "title" else str(value)


def rewrite_post_document(
    original: str,
    fields: dict[str, Any],
    *,
    content: str | None = None,
) -> str:
    _, front_lines, old_body = split_front_matter(original)
    if not front_lines and not original.lstrip().startswith("---"):
        front_lines = []
    output: list[str] = []
    written: set[str] = set()
    index = 0
    while index < len(front_lines):
        line = front_lines[index]
        match = FRONT_MATTER_FIELD_RE.match(line)
        key = match.group(1) if match else ""
        if key in fields:
            if key not in written:
                output.append(f"{key}: {serialized_front_matter_value(key, fields[key])}")
                written.add(key)
            index += 1
            while index < len(front_lines):
                continuation = front_lines[index]
                if re.match(r"^\s*-\s+.+", continuation):
                    index += 1
                    continue
                if continuation.strip() and not continuation[0].isspace():
                    break
                if not continuation.strip() or continuation[0].isspace():
                    index += 1
                    continue
                break
            continue
        output.append(line)
        index += 1
    for key in ("title", "date", "draft", "tags", "categories"):
        if key in fields and key not in written:
            output.append(f"{key}: {serialized_front_matter_value(key, fields[key])}")
    body = old_body if content is None else content.strip()
    return "---\n" + "\n".join(output) + "\n---\n\n" + body.rstrip() + "\n"


def normalized_post_fields(payload: dict[str, Any]) -> dict[str, Any]:
    title = str(payload.get("title", "")).strip()
    if not title:
        raise BlogAdminError("文章标题不能为空。", code="invalid_post")
    if len(title) > 200:
        raise BlogAdminError("文章标题不能超过 200 个字符。", code="invalid_post")
    content = str(payload.get("content", "")).strip()
    if not content:
        raise BlogAdminError("文章正文不能为空。", code="invalid_post")
    date = str(payload.get("date", "")).strip() or dt.date.today().isoformat()
    try:
        parsed_date = dt.date.fromisoformat(date)
    except ValueError as exc:
        raise BlogAdminError("日期必须使用 YYYY-MM-DD。", code="invalid_post") from exc
    if parsed_date.isoformat() != date:
        raise BlogAdminError("日期必须使用 YYYY-MM-DD。", code="invalid_post")
    tags = normalize_string_list(payload.get("tags"), "tags")
    categories = normalize_string_list(payload.get("categories"), "categories")
    if not categories:
        raise BlogAdminError("至少选择一个分类。", code="invalid_post")
    draft_value = payload.get("draft", False)
    if not isinstance(draft_value, bool):
        raise BlogAdminError("草稿状态必须是布尔值。", code="invalid_post")
    return {
        "title": title,
        "date": date,
        "draft": draft_value,
        "tags": tags,
        "categories": categories,
        "content": content,
    }


def post_markdown(
    payload: dict[str, Any], existing_document: str | None = None
) -> tuple[str, str]:
    fields = normalized_post_fields(payload)
    if existing_document is not None:
        document = rewrite_post_document(
            existing_document,
            {key: fields[key] for key in ("title", "date", "draft", "tags", "categories")},
            content=fields["content"],
        )
    else:
        document = (
            "---\n"
            f"title: {yaml_value(fields['title'])}\n"
            f"date: {fields['date']}\n"
            f"draft: {'true' if fields['draft'] else 'false'}\n"
            f"tags: {yaml_list(fields['tags'])}\n"
            f"categories: {yaml_list(fields['categories'])}\n"
            "---\n\n"
            f"{fields['content']}\n"
        )
    return fields["title"], document


def post_path(root: Path, slug: str) -> Path:
    posts_dir = root / "content" / "posts"
    resolved = (posts_dir / f"{validate_slug(slug)}.md").resolve()
    if posts_dir.resolve() not in resolved.parents:
        raise BlogAdminError("文章路径无效。", code="invalid_slug")
    return resolved


def write_post(root: Path, slug: str, markdown: str, overwrite: bool) -> Path:
    path = post_path(root, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise BlogAdminError(
            "同名 Slug 的文章已存在。",
            HTTPStatus.CONFLICT,
            code="slug_conflict",
        )
    atomic_write_text(path, markdown)
    return path


def post_record(path: Path, *, include_content: bool = False) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    metadata, _, body = split_front_matter(raw)
    tags = metadata.get("tags", [])
    categories = metadata.get("categories", [])
    if not isinstance(tags, list):
        tags = [str(tags)] if tags else []
    if not isinstance(categories, list):
        categories = [str(categories)] if categories else []
    modified = dt.datetime.fromtimestamp(
        path.stat().st_mtime, dt.timezone.utc
    ).isoformat()
    record: dict[str, Any] = {
        "slug": path.stem,
        "title": str(metadata.get("title", path.stem)),
        "date": str(metadata.get("date", "")),
        "draft": bool(metadata.get("draft", False)),
        "tags": [str(item) for item in tags],
        "categories": [str(item) for item in categories],
        "updatedAt": modified,
        "wordCount": len(re.sub(r"\s", "", body)),
        "version": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24],
    }
    if include_content:
        record["content"] = body.rstrip()
    return record


def list_posts(root: Path) -> list[dict[str, Any]]:
    posts = root / "content" / "posts"
    if not posts.exists():
        return []
    records = [
        post_record(path)
        for path in posts.glob("*.md")
        if path.name != "_index.md"
    ]
    return sorted(
        records,
        key=lambda item: (str(item.get("date", "")), str(item.get("updatedAt", ""))),
        reverse=True,
    )


def get_post(root: Path, slug: str) -> dict[str, Any]:
    path = post_path(root, slug)
    if not path.exists():
        raise BlogAdminError(
            "文章不存在。", HTTPStatus.NOT_FOUND, code="post_not_found"
        )
    return post_record(path, include_content=True)


def category_usage(root: Path) -> dict[str, int]:
    usage: Counter[str] = Counter()
    for post in list_posts(root):
        usage.update(post["categories"])
    return dict(usage)


def categories_payload(root: Path) -> dict[str, Any]:
    categories = read_categories(root)
    usage = category_usage(root)
    result = [
        {**category, "usageCount": usage.get(category["name"], 0)}
        for category in categories
    ]
    return {"categories": result, "version": categories_version(categories)}


class ContentSnapshot:
    def __init__(self, root: Path, targets: list[str]):
        self.root = root
        self.targets = targets
        self.sealed = False
        self.files: dict[str, bytes] = {}
        for target_name in targets:
            target = root / target_name
            if target.is_file():
                self.files[target_name] = target.read_bytes()
            elif target.is_dir():
                for file_path in target.rglob("*"):
                    if file_path.is_file():
                        self.files[str(file_path.relative_to(root))] = file_path.read_bytes()

    def restore(self) -> None:
        if self.sealed:
            return
        for target_name in self.targets:
            target = self.root / target_name
            if target.is_file() and target_name not in self.files:
                target.unlink()
            elif target.is_dir():
                for file_path in sorted(target.rglob("*"), reverse=True):
                    relative = str(file_path.relative_to(self.root))
                    if file_path.is_file() and relative not in self.files:
                        file_path.unlink()
                for directory in sorted(
                    (path for path in target.rglob("*") if path.is_dir()),
                    reverse=True,
                ):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
        for relative, content in self.files.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    def seal(self) -> None:
        self.sealed = True


def prepare_site_build(root: Path) -> tuple[Path, Path | None]:
    target = public_dir()
    parent = target.parent if target else Path(tempfile.gettempdir())
    parent.mkdir(parents=True, exist_ok=True)
    destination = Path(
        tempfile.mkdtemp(prefix=".blog-admin-build.", dir=str(parent))
    )
    try:
        run(
            ["hugo", "--gc", "--minify", "--destination", str(destination)],
            root,
            timeout=300,
            public_message="Hugo 构建校验失败，请检查文章内容。",
        )
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination, target


def activate_site_build(build: Path, target: Path | None) -> None:
    if target is None:
        shutil.rmtree(build, ignore_errors=True)
        return
    backup = target.parent / f".{target.name}.previous.{secrets.token_hex(6)}"
    moved_old = False
    try:
        if target.exists():
            os.replace(target, backup)
            moved_old = True
        os.replace(build, target)
        if moved_old:
            shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        if not target.exists() and moved_old and backup.exists():
            os.replace(backup, target)
        raise
    finally:
        shutil.rmtree(build, ignore_errors=True)


def unstage_content(root: Path, paths: list[str]) -> None:
    subprocess.run(
        ["git", "reset", "--quiet", "--", *paths],
        cwd=str(root),
        env=git_env(),
        capture_output=True,
        check=False,
    )


def commit_changes(root: Path, message: str, paths: list[str]) -> str:
    proc_env = git_env()
    run(
        ["git", "add", "--", *paths],
        root,
        proc_env=proc_env,
        public_message="无法暂存内容修改。",
    )
    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--", *paths],
        cwd=str(root),
        env=proc_env,
        check=False,
    )
    if diff.returncode == 0:
        return ""
    if diff.returncode != 1:
        raise BlogAdminError(
            "无法检查待发布内容。",
            HTTPStatus.INTERNAL_SERVER_ERROR,
            code="git_diff_failed",
        )
    run(
        [
            "git",
            "-c",
            "user.name=Blog Admin",
            "-c",
            "user.email=blog-admin@shcxyz.site",
            "commit",
            "-m",
            message,
        ],
        root,
        proc_env=proc_env,
        public_message="无法提交内容修改。",
    )
    return run(
        ["git", "rev-parse", "--short", "HEAD"],
        root,
        proc_env=proc_env,
        public_message="无法读取提交版本。",
    ).stdout.strip()


def finish_content_change(
    root: Path,
    snapshot: ContentSnapshot,
    *,
    message: str,
    paths: list[str],
) -> str:
    build: Path | None = None
    target: Path | None = None
    committed = False
    try:
        build, target = prepare_site_build(root)
        commit = commit_changes(root, message, paths)
        committed = bool(commit)
        if committed:
            snapshot.seal()
        push_pending_commits(root)
        activate_site_build(build, target)
        build = None
        snapshot.seal()
        return commit or "no-change"
    except Exception:
        if not committed:
            snapshot.restore()
            unstage_content(root, paths)
        raise
    finally:
        if build is not None:
            shutil.rmtree(build, ignore_errors=True)


def validate_selected_categories(root: Path, selected: list[str]) -> None:
    catalog = {category["name"] for category in read_categories(root)}
    missing = [name for name in selected if name not in catalog]
    if missing:
        raise BlogAdminError(
            f"文章引用了不存在的分类：{', '.join(missing)}",
            HTTPStatus.CONFLICT,
            code="category_missing",
        )


def create_post(payload: dict[str, Any]) -> dict[str, Any]:
    with publish_lock:
        root = ensure_source_repo(force=True)
        ensure_content_clean(root)
        require_publish_queue_empty(root)
        slug = validate_slug(str(payload.get("slug", "")))
        fields = normalized_post_fields(payload)
        validate_selected_categories(root, fields["categories"])
        title, markdown = post_markdown(payload)
        path = post_path(root, slug)
        if path.exists():
            raise BlogAdminError(
                "同名 Slug 的文章已存在。",
                HTTPStatus.CONFLICT,
                code="slug_conflict",
            )
        snapshot = ContentSnapshot(root, ["content/posts"])
        try:
            write_post(root, slug, markdown, overwrite=False)
            commit = finish_content_change(
                root,
                snapshot,
                message=f"Create blog post: {title}",
                paths=["content/posts"],
            )
        except Exception:
            snapshot.restore()
            raise
        return {**get_post(root, slug), "commit": commit}


def update_post(slug: str, payload: dict[str, Any]) -> dict[str, Any]:
    with publish_lock:
        root = ensure_source_repo(force=True)
        ensure_content_clean(root)
        require_publish_queue_empty(root)
        current_path = post_path(root, slug)
        if not current_path.exists():
            raise BlogAdminError(
                "文章不存在。", HTTPStatus.NOT_FOUND, code="post_not_found"
            )
        current = post_record(current_path, include_content=True)
        if str(payload.get("version", "")) != current["version"]:
            raise BlogAdminError(
                "文章已被其他会话修改，请刷新后重试。",
                HTTPStatus.CONFLICT,
                code="version_conflict",
            )
        new_slug = validate_slug(str(payload.get("slug", slug)))
        new_path = post_path(root, new_slug)
        if new_path != current_path and new_path.exists():
            raise BlogAdminError(
                "新的 Slug 已被其他文章使用。",
                HTTPStatus.CONFLICT,
                code="slug_conflict",
            )
        fields = normalized_post_fields(payload)
        validate_selected_categories(root, fields["categories"])
        raw = current_path.read_text(encoding="utf-8")
        title, markdown = post_markdown(payload, raw)
        snapshot = ContentSnapshot(root, ["content/posts"])
        try:
            atomic_write_text(new_path, markdown)
            if new_path != current_path:
                current_path.unlink()
            commit = finish_content_change(
                root,
                snapshot,
                message=f"Update blog post: {title}",
                paths=["content/posts"],
            )
        except Exception:
            snapshot.restore()
            raise
        return {**get_post(root, new_slug), "commit": commit}


def delete_post(slug: str, expected_version: str) -> dict[str, Any]:
    with publish_lock:
        root = ensure_source_repo(force=True)
        ensure_content_clean(root)
        require_publish_queue_empty(root)
        path = post_path(root, slug)
        if not path.exists():
            raise BlogAdminError(
                "文章不存在。", HTTPStatus.NOT_FOUND, code="post_not_found"
            )
        record = post_record(path)
        if not expected_version or expected_version.strip('"') != record["version"]:
            raise BlogAdminError(
                "文章已被其他会话修改，请刷新后重试。",
                HTTPStatus.CONFLICT,
                code="version_conflict",
            )
        snapshot = ContentSnapshot(root, ["content/posts"])
        try:
            path.unlink()
            commit = finish_content_change(
                root,
                snapshot,
                message=f"Delete blog post: {record['title']}",
                paths=["content/posts"],
            )
        except Exception:
            snapshot.restore()
            raise
        return {"ok": True, "slug": slug, "commit": commit}


def category_content_dir(root: Path, name: str) -> Path:
    base = (root / "content" / "categories").resolve()
    path = (base / validate_category_name(name)).resolve()
    if base not in path.parents:
        raise BlogAdminError("分类路径无效。", code="invalid_category")
    return path


def migrate_category_directories(
    root: Path,
    *,
    removed: list[dict[str, str]],
    renames: dict[str, str],
) -> None:
    base = root / "content" / "categories"
    base.mkdir(parents=True, exist_ok=True)
    for item in removed:
        directory = category_content_dir(root, item["name"])
        if directory.exists():
            shutil.rmtree(directory)

    staged: list[tuple[Path, str]] = []
    for old_name, new_name in renames.items():
        old_directory = category_content_dir(root, old_name)
        if not old_directory.exists():
            continue
        temporary = base / f".admin-category-rename-{secrets.token_hex(8)}"
        os.replace(old_directory, temporary)
        staged.append((temporary, new_name))

    for temporary, new_name in staged:
        destination = category_content_dir(root, new_name)
        if destination.exists():
            raise BlogAdminError(
                f"分类目录已存在，无法迁移资源：{new_name}",
                HTTPStatus.CONFLICT,
                code="category_directory_conflict",
            )
        os.replace(temporary, destination)


def update_categories(payload: dict[str, Any]) -> dict[str, Any]:
    with publish_lock:
        root = ensure_source_repo(force=True)
        ensure_content_clean(root)
        require_publish_queue_empty(root)
        old_categories = read_categories(root)
        expected_version = str(payload.get("version", ""))
        if expected_version != categories_version(old_categories):
            raise BlogAdminError(
                "分类已被其他会话修改，请刷新后重试。",
                HTTPStatus.CONFLICT,
                code="version_conflict",
            )
        new_categories = normalize_categories(payload.get("categories"))
        old_by_id = {item["id"]: item for item in old_categories}
        new_by_id = {item["id"]: item for item in new_categories}
        usage = category_usage(root)
        removed = [
            item
            for identifier, item in old_by_id.items()
            if identifier not in new_by_id
        ]
        used_removed = [
            item["name"] for item in removed if usage.get(item["name"], 0) > 0
        ]
        if used_removed:
            raise BlogAdminError(
                f"以下分类仍被文章使用，需先迁移文章：{', '.join(used_removed)}",
                HTTPStatus.CONFLICT,
                code="category_in_use",
            )
        renames = {
            old_by_id[identifier]["name"]: item["name"]
            for identifier, item in new_by_id.items()
            if identifier in old_by_id
            and old_by_id[identifier]["name"] != item["name"]
        }
        snapshot = ContentSnapshot(
            root, ["hugo.toml", "content/categories", "content/posts"]
        )
        try:
            if renames:
                for path in (root / "content" / "posts").glob("*.md"):
                    raw = path.read_text(encoding="utf-8")
                    metadata, _, _ = split_front_matter(raw)
                    current_values = metadata.get("categories", [])
                    if not isinstance(current_values, list):
                        current_values = [current_values] if current_values else []
                    updated_values = [
                        renames.get(str(value), str(value)) for value in current_values
                    ]
                    if updated_values != [str(value) for value in current_values]:
                        atomic_write_text(
                            path,
                            rewrite_post_document(
                                raw,
                                {
                                    "categories": list(
                                        dict.fromkeys(updated_values)
                                    )
                                },
                            ),
                        )
            migrate_category_directories(
                root, removed=removed, renames=renames
            )
            write_categories(root, new_categories)
            commit = finish_content_change(
                root,
                snapshot,
                message="Update blog categories",
                paths=["hugo.toml", "content/categories", "content/posts"],
            )
        except Exception:
            snapshot.restore()
            raise
        response = categories_payload(root)
        response["commit"] = commit
        return response


def publish(payload: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible helper that no longer rewrites the category catalog."""
    if bool(payload.get("overwrite", False)):
        root = root_for_read()
        slug = validate_slug(str(payload.get("slug", "")))
        current = get_post(root, slug)
        return update_post(slug, {**payload, "version": current["version"]})
    return create_post(payload)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str, *, iterations: int = 310_000) -> str:
    if len(password) < 12:
        raise ValueError("password must contain at least 12 characters")
    salt = secrets.token_bytes(18)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return (
        f"pbkdf2_sha256${iterations}${_b64encode(salt)}${_b64encode(digest)}"
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        if not 200_000 <= iterations <= 2_000_000:
            return False
        expected = _b64decode(digest_text)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            _b64decode(salt_text),
            iterations,
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def session_secret() -> bytes:
    value = os.environ.get("BLOG_ADMIN_SESSION_SECRET")
    return value.encode("utf-8") if value else ephemeral_session_secret


def analytics_secret() -> bytes:
    value = os.environ.get("BLOG_ADMIN_ANALYTICS_SECRET")
    return (value.encode("utf-8") if value else session_secret())


def create_session(username: str) -> tuple[str, dict[str, Any]]:
    now = int(time.time())
    payload = {
        "u": username,
        "iat": now,
        "exp": now + SESSION_TTL_SECONDS,
        "sid": secrets.token_urlsafe(18),
        "csrf": secrets.token_urlsafe(32),
    }
    body = _b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    signature = _b64encode(
        hmac.new(session_secret(), body.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{body}.{signature}", payload


def revocation_file() -> Path | None:
    value = os.environ.get("BLOG_ADMIN_REVOCATION_FILE")
    return Path(value).resolve() if value else None


def load_revocations_locked() -> bool:
    global _revocations_loaded_file

    path = revocation_file()
    key = str(path) if path else ""
    if _revocations_loaded_file == key:
        return True
    if path is None or not path.exists():
        _revocations_loaded_file = key
        return True
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                sid = str(item["sid"])
                expiry = int(item["exp"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if sid and expiry > int(time.time()):
                revoked_sessions[sid] = expiry
        _revocations_loaded_file = key
        return True
    except OSError:
        logger.exception("unable to load persistent session revocations")
        return False


def revoke_session(sid: str, expiry: int) -> None:
    global _revocations_loaded_file

    with login_lock:
        if not load_revocations_locked():
            raise BlogAdminError(
                "无法安全注销，请稍后重试。",
                HTTPStatus.INTERNAL_SERVER_ERROR,
                code="logout_failed",
            )
        revoked_sessions[sid] = expiry
        path = revocation_file()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                0o600,
            )
            try:
                line = (
                    json.dumps(
                        {"sid": sid, "exp": expiry},
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
                os.write(descriptor, line)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.chmod(path, 0o600)
            _revocations_loaded_file = str(path)
        except OSError as exc:
            logger.exception("unable to persist session revocation")
            raise BlogAdminError(
                "无法安全注销，请稍后重试。",
                HTTPStatus.INTERNAL_SERVER_ERROR,
                code="logout_failed",
                detail=str(exc),
            ) from exc


def decode_session(token: str) -> dict[str, Any] | None:
    try:
        body, signature = token.split(".", 1)
        expected = _b64encode(
            hmac.new(
                session_secret(), body.encode("ascii"), hashlib.sha256
            ).digest()
        )
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(_b64decode(body))
        now = int(time.time())
        if int(payload.get("exp", 0)) <= now:
            return None
        sid = str(payload.get("sid", ""))
        with login_lock:
            if not load_revocations_locked():
                return None
            for revoked_sid, expiry in list(revoked_sessions.items()):
                if expiry <= now:
                    revoked_sessions.pop(revoked_sid, None)
            if sid in revoked_sessions:
                return None
        if not all(payload.get(key) for key in ("u", "sid", "csrf")):
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def session_cookie(token: str, *, max_age: int = SESSION_TTL_SECONDS) -> str:
    cookie = SimpleCookie()
    cookie[SESSION_COOKIE] = token
    cookie[SESSION_COOKIE]["path"] = "/admin/"
    if env("BLOG_ADMIN_COOKIE_SECURE", "1") != "0":
        cookie[SESSION_COOKIE]["secure"] = True
    cookie[SESSION_COOKIE]["httponly"] = True
    cookie[SESSION_COOKIE]["samesite"] = "Strict"
    cookie[SESSION_COOKIE]["max-age"] = max_age
    return cookie.output(header="").strip()


def clear_session_cookie() -> str:
    return session_cookie("", max_age=0)


def login_retry_after(client_ip: str) -> int:
    now = time.monotonic()
    with login_lock:
        recent = [
            value
            for value in login_failures.get(client_ip, [])
            if now - value < LOGIN_WINDOW_SECONDS
        ]
        login_failures[client_ip] = recent
        if len(recent) < LOGIN_MAX_FAILURES:
            return 0
        return max(1, int(LOGIN_WINDOW_SECONDS - (now - recent[0])))


def record_login_failure(client_ip: str) -> None:
    with login_lock:
        login_failures[client_ip].append(time.monotonic())


def clear_login_failures(client_ip: str) -> None:
    with login_lock:
        login_failures.pop(client_ip, None)


def audit_event(
    action: str,
    *,
    user: str,
    target: str = "",
    client_ip: str = "",
    result: str = "ok",
) -> None:
    path = Path(
        env("BLOG_ADMIN_AUDIT_LOG", "/var/lib/blog-admin/audit.log")
    ).resolve()
    event = {
        "time": dt.datetime.now(dt.timezone.utc).isoformat(),
        "action": action,
        "user": user,
        "target": target,
        "ip": client_ip,
        "result": result,
    }
    try:
        with audit_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
            os.chmod(path, 0o600)
    except OSError:
        logger.exception("unable to write audit log")


def visitor_fingerprint(ip: str, user_agent: str) -> str:
    value = f"{ip}\0{user_agent}".encode("utf-8", errors="replace")
    return hmac.new(analytics_secret(), value, hashlib.sha256).hexdigest()[:32]


def is_pageview(path: str, method: str, status: int, user_agent: str) -> bool:
    if method != "GET" or status not in {200, 304}:
        return False
    if BOT_RE.search(user_agent):
        return False
    if path.startswith(("/admin", "/analytics", "/api")):
        return False
    if path in {"/favicon.ico", "/robots.txt", "/sitemap.xml"}:
        return False
    return Path(path).suffix.lower() not in STATIC_SUFFIXES


def read_access_log_lines(path: Path) -> tuple[list[str], bool]:
    max_bytes = max(
        1024 * 1024,
        int(env("BLOG_ADMIN_ACCESS_LOG_MAX_BYTES", str(16 * 1024 * 1024))),
    )
    size = path.stat().st_size
    truncated = size > max_bytes
    with path.open("rb") as handle:
        if truncated:
            handle.seek(-max_bytes, os.SEEK_END)
            handle.readline()
        data = handle.read(max_bytes)
    return data.decode("utf-8", errors="replace").splitlines(), truncated


def _parse_access_log_uncached(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError as exc:
        return {
            "status": "unavailable",
            "message": "访问日志尚不可用。",
            "error": str(exc),
            "truncated": False,
            "totalPv": 0,
            "visitors": set(),
            "dailyPv": Counter(),
            "dailyVisitors": defaultdict(set),
            "pagePv": Counter(),
            "pageVisitors": defaultdict(set),
            "referrers": Counter(),
        }

    lines, truncated = read_access_log_lines(path)
    total_pv = 0
    visitors: set[str] = set()
    daily_pv: Counter[str] = Counter()
    daily_visitors: dict[str, set[str]] = defaultdict(set)
    page_pv: Counter[tuple[str, str]] = Counter()
    page_visitors: dict[tuple[str, str], set[str]] = defaultdict(set)
    referrers: Counter[tuple[str, str]] = Counter()
    for line in lines:
        match = ACCESS_LOG_RE.match(line)
        if not match:
            continue
        try:
            stamp = dt.datetime.strptime(
                match.group("time"), "%d/%b/%Y:%H:%M:%S %z"
            )
            status = int(match.group("status"))
        except ValueError:
            continue
        target = urlsplit(match.group("target"))
        path_value = unquote(target.path) or "/"
        user_agent = match.group("ua")
        if not is_pageview(
            path_value, match.group("method"), status, user_agent
        ):
            continue
        day = stamp.astimezone(timezone()).date().isoformat()
        visitor = visitor_fingerprint(match.group("ip"), user_agent)
        total_pv += 1
        visitors.add(visitor)
        daily_pv[day] += 1
        daily_visitors[day].add(visitor)
        page_pv[(day, path_value)] += 1
        page_visitors[(day, path_value)].add(visitor)
        referrer = match.group("referrer")
        if referrer and referrer != "-":
            host = (urlsplit(referrer).hostname or "").lower()
            if host and host not in {"shcxyz.site", "www.shcxyz.site"}:
                referrers[(day, host)] += 1

    result = {
        "status": "ok",
        "message": "",
        "truncated": truncated,
        "totalPv": total_pv,
        "visitors": visitors,
        "dailyPv": daily_pv,
        "dailyVisitors": daily_visitors,
        "pagePv": page_pv,
        "pageVisitors": page_visitors,
        "referrers": referrers,
    }
    return result


def parse_access_log(path: Path) -> dict[str, Any]:
    global _stats_cache_key, _stats_cache_value, _stats_cache_expires_at

    path_key = str(path)
    now = time.monotonic()
    with stats_lock:
        if (
            _stats_cache_key is not None
            and _stats_cache_key[0] == path_key
            and _stats_cache_value is not None
            and now < _stats_cache_expires_at
        ):
            return _stats_cache_value

    with stats_refresh_lock:
        now = time.monotonic()
        with stats_lock:
            if (
                _stats_cache_key is not None
                and _stats_cache_key[0] == path_key
                and _stats_cache_value is not None
                and now < _stats_cache_expires_at
            ):
                return _stats_cache_value

        result = _parse_access_log_uncached(path)
        try:
            stat = path.stat()
            cache_key = (path_key, stat.st_mtime_ns, stat.st_size)
        except OSError:
            cache_key = (path_key, 0, 0)
        try:
            ttl = int(env("BLOG_ADMIN_STATS_CACHE_SECONDS", "60"))
        except ValueError:
            ttl = 60
        ttl = max(5, min(ttl, 300))
        with stats_lock:
            _stats_cache_key = cache_key
            _stats_cache_value = result
            _stats_cache_expires_at = time.monotonic() + ttl
        return result


def site_stats(days: int = 30) -> dict[str, Any]:
    days = max(1, min(days, MAX_STATS_DAYS))
    aggregate = parse_access_log(analytics_log_path())
    today = dt.datetime.now(timezone()).date()
    selected_days = [
        (today - dt.timedelta(days=offset)).isoformat()
        for offset in reversed(range(days))
    ]
    selected = set(selected_days)
    daily = [
        {
            "date": day,
            "pv": int(aggregate["dailyPv"].get(day, 0)),
            "uv": len(aggregate["dailyVisitors"].get(day, set())),
        }
        for day in selected_days
    ]
    period_visitors: set[str] = set()
    top_pv: Counter[str] = Counter()
    top_uv: dict[str, set[str]] = defaultdict(set)
    referrers: Counter[str] = Counter()
    for day in selected_days:
        period_visitors.update(aggregate["dailyVisitors"].get(day, set()))
    for (day, path), count in aggregate["pagePv"].items():
        if day in selected:
            top_pv[path] += count
            top_uv[path].update(aggregate["pageVisitors"].get((day, path), set()))
    for (day, host), count in aggregate["referrers"].items():
        if day in selected:
            referrers[host] += count
    today_key = today.isoformat()
    available_days = sorted(aggregate["dailyPv"])
    return {
        "totals": {
            "pv": int(aggregate["totalPv"]),
            "uv": len(aggregate["visitors"]),
            "todayPv": int(aggregate["dailyPv"].get(today_key, 0)),
            "todayUv": len(aggregate["dailyVisitors"].get(today_key, set())),
            "periodPv": sum(item["pv"] for item in daily),
            "periodUv": len(period_visitors),
        },
        "daily": daily,
        "topPages": [
            {"path": path, "pv": count, "uv": len(top_uv[path])}
            for path, count in top_pv.most_common(10)
        ],
        "referrers": [
            {"host": host, "pv": count}
            for host, count in referrers.most_common(10)
        ],
        "source": {
            "status": aggregate["status"],
            "message": aggregate["message"],
            "truncated": bool(aggregate["truncated"]),
            "scope": "available-access-log",
            "rangeStart": available_days[0] if available_days else None,
            "rangeEnd": available_days[-1] if available_days else None,
            "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        },
    }


def overview_payload(root: Path) -> dict[str, Any]:
    posts = list_posts(root)
    categories = read_categories(root)
    stats = site_stats(7)
    return {
        "totals": {
            **stats["totals"],
            "posts": len(posts),
            "categories": len(categories),
        },
        "recentPosts": posts[:5],
        "trend": stats["daily"],
        "source": stats["source"],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "BlogAdmin"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info(
            "%s - [%s] %s",
            self.client_ip(),
            self.log_date_time_string(),
            fmt % args,
        )

    def client_ip(self) -> str:
        peer = self.client_address[0]
        candidate = peer
        if peer in {"127.0.0.1", "::1"}:
            candidate = self.headers.get("X-Real-IP", peer).split(",", 1)[0].strip()
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            return peer

    def send_json(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        cache_control: str = "no-store",
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if headers:
            for name, value in headers.items():
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self, *, limit: int = REQUEST_LIMIT_BYTES) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            raise BlogAdminError(
                "请求必须使用 application/json。",
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                code="invalid_content_type",
            )
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError as exc:
            raise BlogAdminError(
                "Content-Length 无效。", code="invalid_request"
            ) from exc
        if length <= 0:
            raise BlogAdminError("请求体不能为空。", code="invalid_request")
        if length > limit:
            raise BlogAdminError(
                "请求体过大。",
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                code="request_too_large",
            )
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise BlogAdminError("请求体不完整。", code="invalid_request")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BlogAdminError("JSON 格式无效。", code="invalid_json") from exc
        if not isinstance(payload, dict):
            raise BlogAdminError("JSON 请求体必须是对象。", code="invalid_json")
        return payload

    def current_session(self) -> dict[str, Any] | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        morsel = cookie.get(SESSION_COOKIE)
        return decode_session(morsel.value) if morsel else None

    def require_session(self, *, write: bool = False) -> dict[str, Any]:
        session = self.current_session()
        if session is None:
            raise BlogAdminError(
                "登录已失效，请重新登录。",
                HTTPStatus.UNAUTHORIZED,
                code="unauthorized",
            )
        if write:
            origin = self.headers.get("Origin")
            allowed = {
                value.rstrip("/")
                for value in env(
                    "BLOG_ADMIN_ALLOWED_ORIGINS",
                    "https://shcxyz.site,https://www.shcxyz.site",
                ).split(",")
                if value.strip()
            }
            if origin and origin.rstrip("/") not in allowed:
                raise BlogAdminError(
                    "请求来源不受信任。",
                    HTTPStatus.FORBIDDEN,
                    code="invalid_origin",
                )
            fetch_site = self.headers.get("Sec-Fetch-Site", "")
            if fetch_site and fetch_site not in {"same-origin", "none"}:
                raise BlogAdminError(
                    "跨站请求已拒绝。",
                    HTTPStatus.FORBIDDEN,
                    code="cross_site_request",
                )
            supplied = self.headers.get("X-CSRF-Token", "")
            if not hmac.compare_digest(supplied, str(session["csrf"])):
                raise BlogAdminError(
                    "安全令牌无效，请刷新后重试。",
                    HTTPStatus.FORBIDDEN,
                    code="invalid_csrf",
                )
        return session

    def post_slug_from_path(self, path: str) -> str | None:
        prefix = "/api/posts/"
        if not path.startswith(prefix):
            return None
        value = unquote(path[len(prefix) :])
        return validate_slug(value) if value else None

    def handle_login(self) -> None:
        client_ip = self.client_ip()
        retry_after = login_retry_after(client_ip)
        if retry_after:
            raise BlogAdminError(
                f"登录尝试过多，请在 {retry_after} 秒后重试。",
                HTTPStatus.TOO_MANY_REQUESTS,
                code="login_rate_limited",
            )
        origin = self.headers.get("Origin")
        allowed = {
            value.rstrip("/")
            for value in env(
                "BLOG_ADMIN_ALLOWED_ORIGINS",
                "https://shcxyz.site,https://www.shcxyz.site",
            ).split(",")
            if value.strip()
        }
        if origin and origin.rstrip("/") not in allowed:
            raise BlogAdminError(
                "请求来源不受信任。",
                HTTPStatus.FORBIDDEN,
                code="invalid_origin",
            )
        payload = self.read_json(limit=LOGIN_REQUEST_LIMIT_BYTES)
        supplied_user = str(payload.get("username", ""))
        supplied_password = str(payload.get("password", ""))
        configured_user = env("BLOG_ADMIN_USER", "admin")
        configured_hash = os.environ.get("BLOG_ADMIN_PASSWORD_HASH", "")
        if not configured_hash or not os.environ.get("BLOG_ADMIN_SESSION_SECRET"):
            raise BlogAdminError(
                "管理端登录尚未完成安全配置。",
                HTTPStatus.SERVICE_UNAVAILABLE,
                code="auth_not_configured",
            )
        valid_password = verify_password(supplied_password, configured_hash)
        valid_user = hmac.compare_digest(supplied_user, configured_user)
        if not (configured_hash and valid_user and valid_password):
            record_login_failure(client_ip)
            audit_event(
                "login",
                user=supplied_user or "unknown",
                client_ip=client_ip,
                result="denied",
            )
            time.sleep(0.2)
            raise BlogAdminError(
                "用户名或密码错误。",
                HTTPStatus.UNAUTHORIZED,
                code="invalid_credentials",
            )
        clear_login_failures(client_ip)
        token, session = create_session(configured_user)
        audit_event("login", user=configured_user, client_ip=client_ip)
        self.send_json(
            HTTPStatus.OK,
            {
                "authenticated": True,
                "user": configured_user,
                "csrfToken": session["csrf"],
                "expiresAt": session["exp"],
            },
            headers={"Set-Cookie": session_cookie(token)},
        )

    def dispatch(self, method: str) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"

        if method == "GET" and path == "/health":
            self.send_json(HTTPStatus.OK, {"ok": True})
            return
        if method == "GET" and path == "/analytics/summary":
            stats = site_stats(1)
            self.send_json(
                HTTPStatus.OK,
                {
                    "pv": stats["totals"]["pv"],
                    "uv": stats["totals"]["uv"],
                },
                cache_control="public, max-age=60",
            )
            return
        if method == "GET" and path == "/api/session":
            session = self.current_session()
            if session is None:
                self.send_json(
                    HTTPStatus.OK,
                    {"authenticated": False},
                    headers={"Set-Cookie": clear_session_cookie()},
                )
            else:
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "authenticated": True,
                        "user": session["u"],
                        "csrfToken": session["csrf"],
                        "expiresAt": session["exp"],
                    },
                )
            return
        if method == "POST" and path == "/api/login":
            self.handle_login()
            return

        write = method in {"POST", "PUT", "DELETE", "PATCH"}
        session = self.require_session(write=write)
        user = str(session["u"])
        client_ip = self.client_ip()

        if method == "POST" and path == "/api/logout":
            revoke_session(str(session["sid"]), int(session["exp"]))
            audit_event("logout", user=user, client_ip=client_ip)
            self.send_json(
                HTTPStatus.OK,
                {"ok": True},
                headers={"Set-Cookie": clear_session_cookie()},
            )
            return
        if method == "GET" and path == "/api/publish/status":
            self.send_json(
                HTTPStatus.OK, pending_publish_status(root_for_read())
            )
            return
        if method == "POST" and path == "/api/publish/retry":
            payload = retry_pending_publish()
            audit_event(
                "publish.retry",
                user=user,
                target=payload["branch"],
                client_ip=client_ip,
            )
            self.send_json(HTTPStatus.OK, payload)
            return
        if method == "GET" and path == "/api/overview":
            self.send_json(HTTPStatus.OK, overview_payload(root_for_read()))
            return
        if method == "GET" and path == "/api/posts":
            self.send_json(
                HTTPStatus.OK, {"posts": list_posts(root_for_read())}
            )
            return
        slug = self.post_slug_from_path(path)
        if method == "GET" and slug:
            self.send_json(HTTPStatus.OK, get_post(root_for_read(), slug))
            return
        if method == "POST" and path == "/api/posts":
            payload = create_post(self.read_json())
            audit_event(
                "post.create",
                user=user,
                target=payload["slug"],
                client_ip=client_ip,
            )
            self.send_json(HTTPStatus.CREATED, payload)
            return
        if method == "PUT" and slug:
            payload = update_post(slug, self.read_json())
            audit_event(
                "post.update",
                user=user,
                target=payload["slug"],
                client_ip=client_ip,
            )
            self.send_json(HTTPStatus.OK, payload)
            return
        if method == "DELETE" and slug:
            payload = delete_post(slug, self.headers.get("If-Match", ""))
            audit_event(
                "post.delete",
                user=user,
                target=slug,
                client_ip=client_ip,
            )
            self.send_json(HTTPStatus.OK, payload)
            return
        if method == "POST" and path == "/api/publish":
            payload = publish(self.read_json())
            audit_event(
                "post.publish_legacy",
                user=user,
                target=payload["slug"],
                client_ip=client_ip,
            )
            self.send_json(HTTPStatus.OK, payload)
            return
        if method == "GET" and path == "/api/categories":
            self.send_json(HTTPStatus.OK, categories_payload(root_for_read()))
            return
        if method == "PUT" and path == "/api/categories":
            payload = update_categories(self.read_json())
            audit_event(
                "category.update",
                user=user,
                target=payload["version"],
                client_ip=client_ip,
            )
            self.send_json(HTTPStatus.OK, payload)
            return
        if method == "GET" and path == "/api/stats":
            query = dict(
                part.split("=", 1) if "=" in part else (part, "")
                for part in parsed.query.split("&")
                if part
            )
            try:
                days = int(query.get("days", "30"))
            except ValueError as exc:
                raise BlogAdminError(
                    "days 参数无效。", code="invalid_days"
                ) from exc
            if not 1 <= days <= MAX_STATS_DAYS:
                raise BlogAdminError(
                    f"days 必须在 1 到 {MAX_STATS_DAYS} 之间。",
                    code="invalid_days",
                )
            self.send_json(HTTPStatus.OK, site_stats(days))
            return
        raise BlogAdminError(
            "接口不存在。", HTTPStatus.NOT_FOUND, code="not_found"
        )

    def handle_method(self, method: str) -> None:
        try:
            self.dispatch(method)
        except BlogAdminError as exc:
            if exc.detail:
                logger.error("%s: %s", exc.code, exc.detail)
            headers = (
                {"Retry-After": str(login_retry_after(self.client_ip()))}
                if exc.status == HTTPStatus.TOO_MANY_REQUESTS
                else None
            )
            self.send_json(
                exc.status,
                {"ok": False, "error": str(exc), "code": exc.code},
                headers=headers,
            )
        except Exception:
            trace_id = secrets.token_hex(8)
            logger.exception("unhandled request error trace=%s", trace_id)
            self.send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "ok": False,
                    "error": "服务器内部错误，请稍后重试。",
                    "code": "internal_error",
                    "traceId": trace_id,
                },
            )

    def do_GET(self) -> None:
        self.handle_method("GET")

    def do_POST(self) -> None:
        self.handle_method("POST")

    def do_PUT(self) -> None:
        self.handle_method("PUT")

    def do_DELETE(self) -> None:
        self.handle_method("DELETE")


class BlogAdminHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


def main() -> None:
    logging.basicConfig(
        level=env("BLOG_ADMIN_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not os.environ.get("BLOG_ADMIN_PASSWORD_HASH"):
        logger.error("BLOG_ADMIN_PASSWORD_HASH is missing; login is disabled")
    if not os.environ.get("BLOG_ADMIN_SESSION_SECRET"):
        logger.error("BLOG_ADMIN_SESSION_SECRET is missing; login is disabled")
    host = env("BLOG_ADMIN_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("BLOG_ADMIN_HOST must remain loopback-only")
    port = int(env("BLOG_ADMIN_PORT", "18080"))
    server = BlogAdminHTTPServer((host, port), Handler)
    logger.info("Blog admin API listening on %s:%s", host, port)
    server.serve_forever()


if __name__ == "__main__":
    main()
