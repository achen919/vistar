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
node --experimental-vm-modules scripts/check-admin-modules.mjs

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
if grep -Eq 'listen .*default_server|server_name .*_;' deploy/nginx/shcxyz.site.conf; then
  echo "The site template must not claim the shared Nginx default server." >&2
  exit 1
fi

grep -Fq 'cancel-in-progress: false' .github/workflows/deploy.yml
grep -Fq 'EXPECTED_REMOTE_PATH="/www/wwwroot/blog"' .github/workflows/deploy.yml
grep -Fq 'Refusing symbolic-link deployment path' .github/workflows/deploy.yml
grep -Fq 'Legacy HTTP Basic Auth is still active' .github/workflows/deploy.yml
grep -Fq -- "--exclude '.git'" .github/workflows/deploy.yml
grep -Fq 'release_root="/www/wwwroot/blog-public-releases"' .github/workflows/deploy.yml
grep -Fq 'SOURCE_BACKUP="/www/wwwroot/blog-source-rollbacks/$RELEASE_ID"' .github/workflows/deploy.yml
grep -Fq 'rollback_source_on_error()' .github/workflows/deploy.yml
grep -Fq 'mv -Tf "$next_link" "$current_path"' .github/workflows/deploy.yml
grep -Fq '__missing_module_probe__.js' .github/workflows/deploy.yml
grep -Fq 'setfacl -m "u:${BLOG_ADMIN_SERVICE_USER}:--x"' deploy/install-blog-admin.sh
grep -Fq 'remote set-url origin "${BLOG_ADMIN_REPO_URL}"' deploy/install-blog-admin.sh
grep -Fq 'submodule sync --recursive' deploy/install-blog-admin.sh
grep -Fq 'submodule update --init --recursive' deploy/install-blog-admin.sh
if grep -Fq 'cp -a "${BLOG_DEPLOY_DIR}/themes/PaperMod"' deploy/install-blog-admin.sh; then
  echo "The management checkout must initialize its own PaperMod submodule." >&2
  exit 1
fi
grep -Fq 'location ~* ^/admin/.*\.' deploy/nginx/shcxyz.site.conf

python3 - <<'PY'
from pathlib import Path

installer = Path("deploy/install-blog-admin.sh").read_text(encoding="utf-8")
repair_call = "\n  repair_legacy_theme_checkout\n"
pull_call = 'git -C "${BLOG_ADMIN_SOURCE_DIR}" pull --ff-only'
assert repair_call in installer
assert installer.index(repair_call) < installer.index(pull_call)

nginx = Path("deploy/nginx/shcxyz.site.conf").read_text(encoding="utf-8")
dotfile_deny = "location ~ /\\.(?!well-known)"
admin_assets = "location ~* ^/admin/.*\\."
assert nginx.index(dotfile_deny) < nginx.index(admin_assets)

workflow = Path(".github/workflows/deploy.yml").read_text(encoding="utf-8")
assert workflow.index("Validate admin code before deployment") < workflow.index(
    "rsync -az --delete"
)
assert 'rollback_marker="$release_root/.previous-$release_id"' in workflow
assert "restored the previous public release" in workflow
PY

if grep -REiq 'busuanzi\.ibruce\.info|busuanzi\.pure' layouts static; then
  echo "The third-party Busuanzi script must not be present." >&2
  exit 1
fi

test -f static/admin/index.html
test -f static/admin/admin.js
test -f static/admin/api.js
test -f static/admin/ui.js
test -f static/admin/boot.js
test -f static/admin/version.json
grep -Eq 'styles\.css\?v=[^"]+' static/admin/index.html
grep -Eq 'boot\.js\?v=[^"]+' static/admin/index.html
grep -Eq 'admin\.js\?v=[^"]+' static/admin/index.html
grep -Fq "管理后台未能启动" static/admin/boot.js
grep -Fq "markBootComplete();" static/admin/admin.js
test -f static/js/site-stats.js
