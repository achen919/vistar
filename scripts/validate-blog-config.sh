#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$(mktemp -d /tmp/vistar-blog.XXXXXX)"
trap 'rm -rf "$BUILD_DIR"' EXIT

categories=(
  "技术-后端开发"
  "技术-agent"
  "技术-llm"
  "随笔-胡思乱想"
  "随笔-如何搞钱"
)

cd "$ROOT_DIR"

hugo --quiet --destination "$BUILD_DIR"

grep -Fq "阿辰的博客" "$BUILD_DIR/index.html"
grep -Fq "阿辰的博客" hugo.toml

for category in "${categories[@]}"; do
  grep -Fq "$category" "$BUILD_DIR/categories/index.html"
  test -f "$BUILD_DIR/categories/$category/index.html"
done

grep -Fq "limit_req_zone" deploy/nginx/shcxyz.site.conf
grep -Fq "zone=blog_request_limit" deploy/nginx/shcxyz.site.conf
grep -Fq "limit_conn" deploy/nginx/shcxyz.site.conf
