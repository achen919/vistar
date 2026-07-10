#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$(mktemp -d /tmp/vistar-blog.XXXXXX)"
CATEGORIES_FILE="$(mktemp /tmp/vistar-categories.XXXXXX)"
trap 'rm -rf "$BUILD_DIR" "$CATEGORIES_FILE"' EXIT

cd "$ROOT_DIR"

hugo --quiet --destination "$BUILD_DIR"
python3 -m py_compile tools/blog_admin_server.py
python3 -m unittest tests/test_blog_admin_server.py

python3 - "$CATEGORIES_FILE" <<'PY'
import sys
import tomllib
from pathlib import Path

data = tomllib.loads(Path("hugo.toml").read_text(encoding="utf-8"))
categories = data.get("params", {}).get("blogCategories", [])
Path(sys.argv[1]).write_text(
    "\n".join(str(item.get("name", "")).strip() for item in categories if item.get("name")),
    encoding="utf-8",
)
PY

grep -Fq "阿辰的博客" "$BUILD_DIR/index.html"
grep -Fq "阿辰的博客" hugo.toml

while IFS= read -r category; do
  grep -Fq "$category" "$BUILD_DIR/categories/index.html"
  test -f "$BUILD_DIR/categories/$category/index.html"
done < "$CATEGORIES_FILE"

grep -Fq "limit_req_zone" deploy/nginx/shcxyz.site.conf
grep -Fq "zone=blog_request_limit" deploy/nginx/shcxyz.site.conf
grep -Fq "limit_conn" deploy/nginx/shcxyz.site.conf
grep -Fq "auth_basic_user_file /www/server/nginx/.htpasswd-blog-admin;" deploy/nginx/shcxyz.site.conf
grep -Fq "proxy_pass http://127.0.0.1:18080/api/;" deploy/nginx/shcxyz.site.conf
test -f static/admin/index.html
test -f static/admin/admin.js
