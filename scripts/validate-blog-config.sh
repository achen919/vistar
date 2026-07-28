#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$(mktemp -d /tmp/vistar-blog.XXXXXX)"
CATEGORIES_FILE="$(mktemp /tmp/vistar-categories.XXXXXX)"
trap 'rm -rf "$BUILD_DIR" "$CATEGORIES_FILE"' EXIT

cd "$ROOT_DIR"

hugo --quiet --destination "$BUILD_DIR"
python3 - <<'PY'
from pathlib import Path

source = Path("tools/blog_admin_server.py").read_text(encoding="utf-8")
compile(source, "tools/blog_admin_server.py", "exec")
PY
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v

while IFS= read -r -d '' javascript_file; do
  node --check "$javascript_file"
done < <(find static/admin -type f -name '*.js' -print0)

bash -n \
  deploy/install-blog-admin.sh \
  scripts/blog.sh \
  scripts/validate-blog-config.sh

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
grep -Fq "zone=blog_login_limit" deploy/nginx/shcxyz.site.conf
grep -Fq "limit_conn" deploy/nginx/shcxyz.site.conf
if grep -Eq '^[[:space:]]*auth_basic([[:space:]]|_)' deploy/nginx/shcxyz.site.conf; then
  echo "Legacy Nginx Basic Auth must not be enabled." >&2
  exit 1
fi
grep -Fq "location = /admin/api/login" deploy/nginx/shcxyz.site.conf
grep -Fq "limit_req zone=blog_login_limit" deploy/nginx/shcxyz.site.conf
grep -Fq "proxy_pass http://127.0.0.1:18080/api/;" deploy/nginx/shcxyz.site.conf
grep -Fq "Content-Security-Policy" deploy/nginx/shcxyz.site.conf
grep -Fq "frame-ancestors 'none'" deploy/nginx/shcxyz.site.conf
grep -Fq "location = /analytics/summary" deploy/nginx/shcxyz.site.conf
grep -Fq "proxy_pass http://127.0.0.1:18080/analytics/summary;" deploy/nginx/shcxyz.site.conf

if grep -REiq 'busuanzi\.ibruce\.info|busuanzi\.pure' layouts static; then
  echo "The third-party Busuanzi script must not be present." >&2
  exit 1
fi

test -f static/admin/index.html
test -f static/admin/admin.js
test -f static/admin/api.js
test -f static/admin/ui.js
test -f static/js/site-stats.js
