#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root on the blog server." >&2
  exit 1
fi

BLOG_ADMIN_USER="${BLOG_ADMIN_USER:-admin}"
BLOG_ADMIN_PASSWORD="${BLOG_ADMIN_PASSWORD:-}"
BLOG_ADMIN_SERVICE_USER="blog-admin"
BLOG_ADMIN_STATE_DIR="${BLOG_ADMIN_STATE_DIR:-/var/lib/blog-admin}"
BLOG_ADMIN_SOURCE_DIR="${BLOG_ADMIN_SOURCE_DIR:-/www/wwwroot/blog-admin-source}"
BLOG_ADMIN_REPO_URL="${BLOG_ADMIN_REPO_URL:-git@github.com-vistar-blog-admin:achen919/vistar.git}"
BLOG_ADMIN_BRANCH="${BLOG_ADMIN_BRANCH:-main}"
BLOG_ADMIN_SSH_KEY="${BLOG_ADMIN_SSH_KEY:-${BLOG_ADMIN_STATE_DIR}/.ssh/vistar_blog_admin_ed25519}"
BLOG_ADMIN_UPDATE_SUBMODULES="${BLOG_ADMIN_UPDATE_SUBMODULES:-0}"
BLOG_ADMIN_ACCESS_LOG="${BLOG_ADMIN_ACCESS_LOG:-/www/wwwlogs/blog.log}"
BLOG_ADMIN_LOG_GROUP="${BLOG_ADMIN_LOG_GROUP:-}"
BLOG_ADMIN_ALLOWED_ORIGINS="${BLOG_ADMIN_ALLOWED_ORIGINS:-https://shcxyz.site,https://www.shcxyz.site}"
BLOG_DEPLOY_DIR="${BLOG_DEPLOY_DIR:-/www/wwwroot/blog}"
BLOG_ADMIN_GITHUB_KNOWN_HOSTS_FILE="${BLOG_ADMIN_GITHUB_KNOWN_HOSTS_FILE:-/root/.ssh/known_hosts}"
LEGACY_SSH_KEY="/root/.ssh/vistar_blog_admin_ed25519"
LEGACY_HTPASSWD_FILE="${HTPASSWD_FILE:-/www/server/nginx/.htpasswd-blog-admin}"
ENV_FILE="/etc/blog-admin.env"

if [[ ! "${BLOG_ADMIN_USER}" =~ ^[A-Za-z0-9_.@-]{1,64}$ ]]; then
  echo "BLOG_ADMIN_USER contains unsupported characters." >&2
  exit 1
fi
for required_command in \
  awk chgrp chmod chown cp curl date dirname find getent git hugo id install mv \
  nginx openssl python3 runuser sed seq sleep ssh ssh-keygen stat systemctl \
  tr useradd usermod; do
  if ! command -v "${required_command}" >/dev/null 2>&1; then
    echo "${required_command} is required on the server." >&2
    exit 1
  fi
done

python3 - \
  "${BLOG_ADMIN_STATE_DIR}" \
  "${BLOG_ADMIN_SOURCE_DIR}" \
  "${BLOG_DEPLOY_DIR}" \
  "${BLOG_ADMIN_ACCESS_LOG}" \
  "${BLOG_ADMIN_SSH_KEY}" \
  "${BLOG_ADMIN_GITHUB_KNOWN_HOSTS_FILE}" \
  "${ENV_FILE}" <<'PY'
import os
import re
import sys
from pathlib import Path

(
    state_text,
    source_text,
    deploy_text,
    access_log_text,
    ssh_key_text,
    known_hosts_text,
    env_file_text,
) = sys.argv[1:]


def fail(message: str) -> None:
    raise SystemExit(f"Unsafe installer path: {message}")


def checked_path(value: str, label: str) -> Path:
    if not value.startswith("/") or value == "/":
        fail(f"{label} must be an absolute non-root path: {value}")
    normalized = os.path.normpath(value)
    if normalized != value:
        fail(f"{label} must already be normalized: {value}")
    path = Path(value)
    current = Path("/")
    for component in path.parts[1:]:
        current /= component
        if os.path.lexists(current) and current.is_symlink():
            fail(f"{label} contains a symbolic link: {current}")
    return path


def require_kind(
    path: Path, label: str, kind: str, *, required: bool = False
) -> None:
    if not path.exists():
        if required:
            fail(f"{label} does not exist: {path}")
        return
    valid = path.is_dir() if kind == "directory" else path.is_file()
    if not valid:
        fail(f"{label} must be a regular {kind}: {path}")


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return path != parent
    except ValueError:
        return False


state = checked_path(state_text, "BLOG_ADMIN_STATE_DIR")
source = checked_path(source_text, "BLOG_ADMIN_SOURCE_DIR")
deploy = checked_path(deploy_text, "BLOG_DEPLOY_DIR")
access_log = checked_path(access_log_text, "BLOG_ADMIN_ACCESS_LOG")
ssh_key = checked_path(ssh_key_text, "BLOG_ADMIN_SSH_KEY")
known_hosts = checked_path(
    known_hosts_text, "BLOG_ADMIN_GITHUB_KNOWN_HOSTS_FILE"
)
env_file = checked_path(env_file_text, "ENV_FILE")
ssh_config = checked_path(
    str(state / ".ssh" / "config"), "managed SSH config"
)
managed_known_hosts = checked_path(
    str(state / ".ssh" / "known_hosts"), "managed known_hosts"
)

if state.parent != Path("/var/lib") or not re.fullmatch(
    r"blog-admin(?:-[A-Za-z0-9._-]+)?", state.name
):
    fail("BLOG_ADMIN_STATE_DIR must be /var/lib/blog-admin or a named variant")

source_in_web_root = (
    source.parent == Path("/www/wwwroot")
    and source.name.endswith("-admin-source")
)
source_in_state = source == state / "source"
if not (source_in_web_root or source_in_state):
    fail(
        "BLOG_ADMIN_SOURCE_DIR must be a *-admin-source directory directly "
        "under /www/wwwroot, or STATE_DIR/source"
    )

if deploy != Path("/www/wwwroot/blog"):
    fail(
        "BLOG_DEPLOY_DIR must remain /www/wwwroot/blog because the tracked "
        "systemd and Nginx files use that deployment root"
    )
if source == deploy or source in deploy.parents or deploy in source.parents:
    fail("source and deployment directories must not overlap")

allowed_log_roots = (Path("/www/wwwlogs"), Path("/var/log/nginx"))
if (
    not any(is_within(access_log, root) for root in allowed_log_roots)
    or access_log.suffix != ".log"
):
    fail(
        "BLOG_ADMIN_ACCESS_LOG must be a .log file under /www/wwwlogs "
        "or /var/log/nginx"
    )
if ssh_key.parent != state / ".ssh" or not re.fullmatch(
    r"[A-Za-z0-9._-]+", ssh_key.name
):
    fail("BLOG_ADMIN_SSH_KEY must be a plain file directly under STATE_DIR/.ssh")
if not (
    is_within(known_hosts, Path("/root/.ssh"))
    or is_within(known_hosts, Path("/etc/ssh"))
):
    fail(
        "BLOG_ADMIN_GITHUB_KNOWN_HOSTS_FILE must be under /root/.ssh or /etc/ssh"
    )
if env_file != Path("/etc/blog-admin.env"):
    fail("ENV_FILE must remain /etc/blog-admin.env")

require_kind(state, "BLOG_ADMIN_STATE_DIR", "directory")
require_kind(source, "BLOG_ADMIN_SOURCE_DIR", "directory")
require_kind(deploy, "BLOG_DEPLOY_DIR", "directory", required=True)
require_kind(access_log, "BLOG_ADMIN_ACCESS_LOG", "file")
require_kind(ssh_key, "BLOG_ADMIN_SSH_KEY", "file")
require_kind(ssh_config, "managed SSH config", "file")
require_kind(managed_known_hosts, "managed known_hosts", "file")
require_kind(
    known_hosts,
    "BLOG_ADMIN_GITHUB_KNOWN_HOSTS_FILE",
    "file",
)
require_kind(env_file, "ENV_FILE", "file")
PY

if ! id -u "${BLOG_ADMIN_SERVICE_USER}" >/dev/null 2>&1; then
  useradd \
    --system \
    --user-group \
    --home-dir "${BLOG_ADMIN_STATE_DIR}" \
    --create-home \
    --shell /usr/sbin/nologin \
    "${BLOG_ADMIN_SERVICE_USER}"
else
  SERVICE_HOME="$(
    getent passwd "${BLOG_ADMIN_SERVICE_USER}" | awk -F: '{print $6; exit}'
  )"
  if [[ "${SERVICE_HOME}" != "${BLOG_ADMIN_STATE_DIR}" ]]; then
    echo "${BLOG_ADMIN_SERVICE_USER} already exists with unexpected home: ${SERVICE_HOME}" >&2
    echo "Expected: ${BLOG_ADMIN_STATE_DIR}" >&2
    exit 1
  fi
  if ! getent group "${BLOG_ADMIN_SERVICE_USER}" >/dev/null 2>&1; then
    echo "Required service group does not exist: ${BLOG_ADMIN_SERVICE_USER}" >&2
    exit 1
  fi
fi

install -d \
  -o "${BLOG_ADMIN_SERVICE_USER}" \
  -g "${BLOG_ADMIN_SERVICE_USER}" \
  -m 700 \
  "${BLOG_ADMIN_STATE_DIR}" \
  "${BLOG_ADMIN_STATE_DIR}/.ssh"

CREATED_DEPLOY_KEY=0
if [[ ! -f "${BLOG_ADMIN_SSH_KEY}" ]]; then
  if [[ -f "${LEGACY_SSH_KEY}" ]]; then
    if [[ -L "${LEGACY_SSH_KEY}" ]]; then
      echo "Refusing symbolic-link legacy SSH key: ${LEGACY_SSH_KEY}" >&2
      exit 1
    fi
    install \
      -o "${BLOG_ADMIN_SERVICE_USER}" \
      -g "${BLOG_ADMIN_SERVICE_USER}" \
      -m 600 \
      "${LEGACY_SSH_KEY}" \
      "${BLOG_ADMIN_SSH_KEY}"
    if [[ -f "${LEGACY_SSH_KEY}.pub" ]]; then
      if [[ -L "${LEGACY_SSH_KEY}.pub" ]]; then
        echo "Refusing symbolic-link legacy public key: ${LEGACY_SSH_KEY}.pub" >&2
        exit 1
      fi
      install \
        -o "${BLOG_ADMIN_SERVICE_USER}" \
        -g "${BLOG_ADMIN_SERVICE_USER}" \
        -m 644 \
        "${LEGACY_SSH_KEY}.pub" \
        "${BLOG_ADMIN_SSH_KEY}.pub"
    fi
  else
    runuser -u "${BLOG_ADMIN_SERVICE_USER}" -- \
      ssh-keygen -t ed25519 -N "" -C "vistar-blog-admin" -f "${BLOG_ADMIN_SSH_KEY}" >/dev/null
    CREATED_DEPLOY_KEY=1
  fi
fi
chown "${BLOG_ADMIN_SERVICE_USER}:${BLOG_ADMIN_SERVICE_USER}" "${BLOG_ADMIN_SSH_KEY}"
chmod 600 "${BLOG_ADMIN_SSH_KEY}"

if [[ "${CREATED_DEPLOY_KEY}" == "1" ]]; then
  echo "Created a new repository deploy key:"
  sed -n '1p' "${BLOG_ADMIN_SSH_KEY}.pub"
  echo
  echo "Bootstrap paused before cloning or changing the running service."
  echo "1. Add the public key above to achen919/vistar as a writable deploy key."
  echo "2. Prepare a trusted file containing GitHub's verified SSH host key."
  echo "3. Set BLOG_ADMIN_GITHUB_KNOWN_HOSTS_FILE to that file and rerun this installer."
  echo "Never trust ssh-keyscan output until its fingerprint matches GitHub's published fingerprints."
  exit 2
fi

if [[ -z "${BLOG_ADMIN_PASSWORD}" ]]; then
  echo "BLOG_ADMIN_PASSWORD is required after deploy-key bootstrap." >&2
  exit 1
fi
if (( ${#BLOG_ADMIN_PASSWORD} < 12 )); then
  echo "BLOG_ADMIN_PASSWORD must contain at least 12 characters." >&2
  exit 1
fi

if [[ -f "${BLOG_ADMIN_GITHUB_KNOWN_HOSTS_FILE}" ]] && \
  ssh-keygen -F github.com -f "${BLOG_ADMIN_GITHUB_KNOWN_HOSTS_FILE}" >/dev/null 2>&1; then
  install \
    -o "${BLOG_ADMIN_SERVICE_USER}" \
    -g "${BLOG_ADMIN_SERVICE_USER}" \
    -m 600 \
    "${BLOG_ADMIN_GITHUB_KNOWN_HOSTS_FILE}" \
    "${BLOG_ADMIN_STATE_DIR}/.ssh/known_hosts"
else
  echo "A trusted known_hosts file containing github.com is required." >&2
  echo "Set BLOG_ADMIN_GITHUB_KNOWN_HOSTS_FILE after verifying GitHub's published SSH fingerprints." >&2
  exit 1
fi

SSH_CONFIG="${BLOG_ADMIN_STATE_DIR}/.ssh/config"
{
  printf '%s\n' "Host github.com-vistar-blog-admin"
  printf '%s\n' "  HostName github.com"
  printf '%s\n' "  User git"
  printf '%s\n' "  IdentityFile ${BLOG_ADMIN_SSH_KEY}"
  printf '%s\n' "  IdentitiesOnly yes"
  printf '%s\n' "  StrictHostKeyChecking yes"
  printf '%s\n' "  UserKnownHostsFile ${BLOG_ADMIN_STATE_DIR}/.ssh/known_hosts"
} > "${SSH_CONFIG}"
chown "${BLOG_ADMIN_SERVICE_USER}:${BLOG_ADMIN_SERVICE_USER}" "${SSH_CONFIG}"
chmod 600 "${SSH_CONFIG}"

PASSWORD_HASH="$(
  BLOG_ADMIN_PASSWORD="${BLOG_ADMIN_PASSWORD}" python3 - <<'PY'
import base64
import hashlib
import os
import secrets

password = os.environ["BLOG_ADMIN_PASSWORD"].encode("utf-8")
salt = secrets.token_bytes(18)
iterations = 310_000
digest = hashlib.pbkdf2_hmac("sha256", password, salt, iterations)
encode = lambda value: base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
print(f"pbkdf2_sha256${iterations}${encode(salt)}${encode(digest)}")
PY
)"

existing_env_value() {
  local key="$1"
  if [[ -f "${ENV_FILE}" ]]; then
    awk -F= -v key="${key}" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "${ENV_FILE}"
  fi
}

SESSION_SECRET="$(existing_env_value BLOG_ADMIN_SESSION_SECRET)"
ANALYTICS_SECRET="$(existing_env_value BLOG_ADMIN_ANALYTICS_SECRET)"
if [[ -z "${SESSION_SECRET}" ]]; then
  SESSION_SECRET="$(openssl rand -base64 48 | tr -d '\n')"
fi
if [[ -z "${ANALYTICS_SECRET}" ]]; then
  ANALYTICS_SECRET="$(openssl rand -base64 48 | tr -d '\n')"
fi

{
  printf 'BLOG_ADMIN_HOST=127.0.0.1\n'
  printf 'BLOG_ADMIN_PORT=18080\n'
  printf 'BLOG_ADMIN_USER=%s\n' "${BLOG_ADMIN_USER}"
  printf 'BLOG_ADMIN_PASSWORD_HASH=%s\n' "${PASSWORD_HASH}"
  printf 'BLOG_ADMIN_SESSION_SECRET=%s\n' "${SESSION_SECRET}"
  printf 'BLOG_ADMIN_ANALYTICS_SECRET=%s\n' "${ANALYTICS_SECRET}"
  printf 'BLOG_ADMIN_ALLOWED_ORIGINS=%s\n' "${BLOG_ADMIN_ALLOWED_ORIGINS}"
  printf 'BLOG_ADMIN_SOURCE_DIR=%s\n' "${BLOG_ADMIN_SOURCE_DIR}"
  printf 'BLOG_ADMIN_REPO_URL=%s\n' "${BLOG_ADMIN_REPO_URL}"
  printf 'BLOG_ADMIN_BRANCH=%s\n' "${BLOG_ADMIN_BRANCH}"
  printf 'BLOG_ADMIN_SSH_KEY=%s\n' "${BLOG_ADMIN_SSH_KEY}"
  printf 'BLOG_ADMIN_UPDATE_SUBMODULES=%s\n' "${BLOG_ADMIN_UPDATE_SUBMODULES}"
  printf 'BLOG_ADMIN_ACCESS_LOG=%s\n' "${BLOG_ADMIN_ACCESS_LOG}"
  printf 'BLOG_ADMIN_STATS_CACHE_SECONDS=60\n'
  printf 'BLOG_ADMIN_TIMEZONE=Asia/Shanghai\n'
  printf 'BLOG_ADMIN_AUDIT_LOG=%s/audit.log\n' "${BLOG_ADMIN_STATE_DIR}"
  printf 'BLOG_ADMIN_REVOCATION_FILE=%s/revoked-sessions.jsonl\n' "${BLOG_ADMIN_STATE_DIR}"
} > "${ENV_FILE}"
chmod 600 "${ENV_FILE}"
unset BLOG_ADMIN_PASSWORD PASSWORD_HASH SESSION_SECRET ANALYTICS_SECRET

if [[ -z "${BLOG_ADMIN_LOG_GROUP}" && -f "${BLOG_ADMIN_ACCESS_LOG}" ]]; then
  FILE_LOG_GROUP="$(stat -c '%G' "${BLOG_ADMIN_ACCESS_LOG}")"
  if [[ "${FILE_LOG_GROUP}" != "root" ]] && getent group "${FILE_LOG_GROUP}" >/dev/null 2>&1; then
    BLOG_ADMIN_LOG_GROUP="${FILE_LOG_GROUP}"
  fi
fi
if [[ -z "${BLOG_ADMIN_LOG_GROUP}" ]]; then
  for candidate in www www-data nginx; do
    if getent group "${candidate}" >/dev/null 2>&1; then
      BLOG_ADMIN_LOG_GROUP="${candidate}"
      break
    fi
  done
fi
if [[ -n "${BLOG_ADMIN_LOG_GROUP}" ]]; then
  if ! getent group "${BLOG_ADMIN_LOG_GROUP}" >/dev/null 2>&1; then
    echo "Unknown BLOG_ADMIN_LOG_GROUP: ${BLOG_ADMIN_LOG_GROUP}" >&2
    exit 1
  fi
  usermod -a -G "${BLOG_ADMIN_LOG_GROUP}" "${BLOG_ADMIN_SERVICE_USER}"
  if [[ -f "${BLOG_ADMIN_ACCESS_LOG}" ]]; then
    chgrp "${BLOG_ADMIN_LOG_GROUP}" "${BLOG_ADMIN_ACCESS_LOG}"
    chmod 640 "${BLOG_ADMIN_ACCESS_LOG}"
  fi
elif [[ -f "${BLOG_ADMIN_ACCESS_LOG}" ]] && command -v setfacl >/dev/null 2>&1; then
  setfacl -m "u:${BLOG_ADMIN_SERVICE_USER}:r" "${BLOG_ADMIN_ACCESS_LOG}"
fi
if [[ -f "${BLOG_ADMIN_ACCESS_LOG}" ]] && \
  ! runuser -u "${BLOG_ADMIN_SERVICE_USER}" -- test -r "${BLOG_ADMIN_ACCESS_LOG}" && \
  command -v setfacl >/dev/null 2>&1; then
  case "${BLOG_ADMIN_ACCESS_LOG}" in
    /www/wwwlogs/*)
      LOG_ACL_STOP="/www"
      ;;
    /var/log/nginx/*)
      LOG_ACL_STOP="/var/log"
      ;;
    *)
      echo "Refusing ACL changes outside the validated log roots." >&2
      exit 1
      ;;
  esac
  LOG_ACL_DIR="$(dirname "${BLOG_ADMIN_ACCESS_LOG}")"
  while [[ "${LOG_ACL_DIR}" != "${LOG_ACL_STOP}" ]]; do
    if ! runuser -u "${BLOG_ADMIN_SERVICE_USER}" -- test -x "${LOG_ACL_DIR}"; then
      setfacl -m "u:${BLOG_ADMIN_SERVICE_USER}:--x" "${LOG_ACL_DIR}"
    fi
    LOG_ACL_DIR="$(dirname "${LOG_ACL_DIR}")"
  done
fi
if [[ -f "${BLOG_ADMIN_ACCESS_LOG}" ]] && \
  ! runuser -u "${BLOG_ADMIN_SERVICE_USER}" -- test -r "${BLOG_ADMIN_ACCESS_LOG}"; then
  echo "${BLOG_ADMIN_SERVICE_USER} cannot read ${BLOG_ADMIN_ACCESS_LOG}." >&2
  echo "Set BLOG_ADMIN_LOG_GROUP and configure log rotation to preserve that group." >&2
  exit 1
elif [[ ! -f "${BLOG_ADMIN_ACCESS_LOG}" ]]; then
  echo "Warning: ${BLOG_ADMIN_ACCESS_LOG} does not exist yet; analytics will be unavailable until it is readable." >&2
fi
if [[ -f "${LEGACY_HTPASSWD_FILE}" ]]; then
  echo "The legacy htpasswd file is unchanged; remove it only after the new login is verified."
fi

GIT_SSH_COMMAND="ssh -F ${SSH_CONFIG}"
if [[ ! -d "${BLOG_ADMIN_SOURCE_DIR}/.git" ]]; then
  if [[ -e "${BLOG_ADMIN_SOURCE_DIR}" ]] && [[ -n "$(find "${BLOG_ADMIN_SOURCE_DIR}" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "${BLOG_ADMIN_SOURCE_DIR} exists and is not an empty Git repository." >&2
    exit 1
  fi
  install -d \
    -o "${BLOG_ADMIN_SERVICE_USER}" \
    -g "${BLOG_ADMIN_SERVICE_USER}" \
    -m 750 \
    "${BLOG_ADMIN_SOURCE_DIR}"
  runuser -u "${BLOG_ADMIN_SERVICE_USER}" -- env \
    HOME="${BLOG_ADMIN_STATE_DIR}" \
    GIT_SSH_COMMAND="${GIT_SSH_COMMAND}" \
    git clone --branch "${BLOG_ADMIN_BRANCH}" "${BLOG_ADMIN_REPO_URL}" "${BLOG_ADMIN_SOURCE_DIR}"
else
  chown -R "${BLOG_ADMIN_SERVICE_USER}:${BLOG_ADMIN_SERVICE_USER}" "${BLOG_ADMIN_SOURCE_DIR}"
  runuser -u "${BLOG_ADMIN_SERVICE_USER}" -- \
    git -C "${BLOG_ADMIN_SOURCE_DIR}" remote set-url origin "${BLOG_ADMIN_REPO_URL}"
  runuser -u "${BLOG_ADMIN_SERVICE_USER}" -- env \
    HOME="${BLOG_ADMIN_STATE_DIR}" \
    GIT_SSH_COMMAND="${GIT_SSH_COMMAND}" \
    git -C "${BLOG_ADMIN_SOURCE_DIR}" pull --ff-only origin "${BLOG_ADMIN_BRANCH}"
fi

if [[ "${BLOG_ADMIN_UPDATE_SUBMODULES}" != "0" ]]; then
  runuser -u "${BLOG_ADMIN_SERVICE_USER}" -- env \
    HOME="${BLOG_ADMIN_STATE_DIR}" \
    GIT_SSH_COMMAND="${GIT_SSH_COMMAND}" \
    git -C "${BLOG_ADMIN_SOURCE_DIR}" submodule update --init --recursive
elif [[ -d "${BLOG_DEPLOY_DIR}/themes/PaperMod" && ! -f "${BLOG_ADMIN_SOURCE_DIR}/themes/PaperMod/theme.toml" ]]; then
  install -d \
    -o "${BLOG_ADMIN_SERVICE_USER}" \
    -g "${BLOG_ADMIN_SERVICE_USER}" \
    -m 750 \
    "${BLOG_ADMIN_SOURCE_DIR}/themes"
  if [[ -e "${BLOG_ADMIN_SOURCE_DIR}/themes/PaperMod" ]]; then
    mv \
      "${BLOG_ADMIN_SOURCE_DIR}/themes/PaperMod" \
      "${BLOG_ADMIN_STATE_DIR}/PaperMod.incomplete.$(date +%s)"
  fi
  cp -a "${BLOG_DEPLOY_DIR}/themes/PaperMod" "${BLOG_ADMIN_SOURCE_DIR}/themes/PaperMod"
  chown -R \
    "${BLOG_ADMIN_SERVICE_USER}:${BLOG_ADMIN_SERVICE_USER}" \
    "${BLOG_ADMIN_SOURCE_DIR}/themes/PaperMod"
fi

install -m 644 \
  "${BLOG_DEPLOY_DIR}/deploy/systemd/blog-admin.service" \
  /etc/systemd/system/blog-admin.service
systemctl daemon-reload
systemctl enable --now blog-admin.service
systemctl restart blog-admin.service

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
