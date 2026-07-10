#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root on the blog server." >&2
  exit 1
fi

BLOG_ADMIN_USER="${BLOG_ADMIN_USER:-achen919}"
BLOG_ADMIN_PASSWORD="${BLOG_ADMIN_PASSWORD:-}"
BLOG_ADMIN_SOURCE_DIR="${BLOG_ADMIN_SOURCE_DIR:-/www/wwwroot/blog-admin-source}"
BLOG_ADMIN_REPO_URL="${BLOG_ADMIN_REPO_URL:-git@github.com-vistar-blog-admin:achen919/vistar.git}"
BLOG_ADMIN_BRANCH="${BLOG_ADMIN_BRANCH:-main}"
BLOG_ADMIN_PUBLIC_DIR="${BLOG_ADMIN_PUBLIC_DIR:-/www/wwwroot/blog/public}"
BLOG_ADMIN_SSH_KEY="${BLOG_ADMIN_SSH_KEY:-/root/.ssh/vistar_blog_admin_ed25519}"
BLOG_ADMIN_UPDATE_SUBMODULES="${BLOG_ADMIN_UPDATE_SUBMODULES:-0}"
BLOG_DEPLOY_DIR="${BLOG_DEPLOY_DIR:-/www/wwwroot/blog}"
HTPASSWD_FILE="${HTPASSWD_FILE:-/www/server/nginx/.htpasswd-blog-admin}"

if [[ -z "${BLOG_ADMIN_PASSWORD}" ]]; then
  echo "BLOG_ADMIN_PASSWORD is required." >&2
  exit 1
fi

if ! command -v hugo >/dev/null 2>&1; then
  echo "hugo is required on the server." >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "git is required on the server." >&2
  exit 1
fi

install -d -m 700 /root/.ssh
if [[ ! -f "${BLOG_ADMIN_SSH_KEY}" ]]; then
  ssh-keygen -t ed25519 -N "" -C "vistar-blog-admin" -f "${BLOG_ADMIN_SSH_KEY}" >/dev/null
  echo "Created SSH key: ${BLOG_ADMIN_SSH_KEY}"
  echo "Add this public key to GitHub as a writable deploy key before rerunning if clone fails:"
  cat "${BLOG_ADMIN_SSH_KEY}.pub"
fi

chmod 600 "${BLOG_ADMIN_SSH_KEY}"
if ! grep -q "Host github.com-vistar-blog-admin" /root/.ssh/config 2>/dev/null; then
  cat >> /root/.ssh/config <<EOF

Host github.com-vistar-blog-admin
  HostName github.com
  User git
  IdentityFile ${BLOG_ADMIN_SSH_KEY}
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
EOF
  chmod 600 /root/.ssh/config
fi

if command -v openssl >/dev/null 2>&1; then
  HASH="$(openssl passwd -apr1 "${BLOG_ADMIN_PASSWORD}")"
else
  echo "openssl is required to generate the htpasswd hash." >&2
  exit 1
fi
install -d -m 755 "$(dirname "${HTPASSWD_FILE}")"
printf '%s:%s\n' "${BLOG_ADMIN_USER}" "${HASH}" > "${HTPASSWD_FILE}"
chmod 644 "${HTPASSWD_FILE}"

cat > /etc/blog-admin.env <<EOF
BLOG_ADMIN_HOST=127.0.0.1
BLOG_ADMIN_PORT=18080
BLOG_ADMIN_SOURCE_DIR=${BLOG_ADMIN_SOURCE_DIR}
BLOG_ADMIN_REPO_URL=${BLOG_ADMIN_REPO_URL}
BLOG_ADMIN_BRANCH=${BLOG_ADMIN_BRANCH}
BLOG_ADMIN_PUBLIC_DIR=${BLOG_ADMIN_PUBLIC_DIR}
BLOG_ADMIN_SSH_KEY=${BLOG_ADMIN_SSH_KEY}
BLOG_ADMIN_UPDATE_SUBMODULES=${BLOG_ADMIN_UPDATE_SUBMODULES}
EOF
chmod 600 /etc/blog-admin.env

if [[ ! -d "${BLOG_ADMIN_SOURCE_DIR}/.git" ]]; then
  rm -rf "${BLOG_ADMIN_SOURCE_DIR}"
  GIT_SSH_COMMAND="ssh -i ${BLOG_ADMIN_SSH_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
    git clone --branch "${BLOG_ADMIN_BRANCH}" "${BLOG_ADMIN_REPO_URL}" "${BLOG_ADMIN_SOURCE_DIR}"
else
  GIT_SSH_COMMAND="ssh -i ${BLOG_ADMIN_SSH_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
    git -C "${BLOG_ADMIN_SOURCE_DIR}" pull --ff-only origin "${BLOG_ADMIN_BRANCH}"
fi

if [[ "${BLOG_ADMIN_UPDATE_SUBMODULES}" != "0" ]]; then
  git -C "${BLOG_ADMIN_SOURCE_DIR}" submodule update --init --recursive
elif [[ -d "${BLOG_DEPLOY_DIR}/themes/PaperMod" && ! -f "${BLOG_ADMIN_SOURCE_DIR}/themes/PaperMod/theme.toml" ]]; then
  mkdir -p "${BLOG_ADMIN_SOURCE_DIR}/themes"
  rm -rf "${BLOG_ADMIN_SOURCE_DIR}/themes/PaperMod"
  cp -a "${BLOG_DEPLOY_DIR}/themes/PaperMod" "${BLOG_ADMIN_SOURCE_DIR}/themes/PaperMod"
fi

cp "${BLOG_DEPLOY_DIR}/deploy/systemd/blog-admin.service" /etc/systemd/system/blog-admin.service
systemctl daemon-reload
systemctl enable --now blog-admin.service

if nginx -t; then
  nginx -s reload
fi

for _ in $(seq 1 30); do
  if curl --fail --silent --show-error http://127.0.0.1:18080/health >/dev/null; then
    echo "Blog admin is installed. Open https://shcxyz.site/admin/"
    exit 0
  fi
  sleep 1
done

systemctl status blog-admin.service --no-pager -l || true
echo "blog-admin.service did not become healthy in time." >&2
exit 1
