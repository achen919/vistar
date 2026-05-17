#!/usr/bin/env bash
set -euo pipefail

BLOG_SERVER="${BLOG_SERVER:-root@62.234.29.88}"

if [[ $# -eq 0 ]]; then
  remote_args=(blog help)
else
  remote_args=(blog "$@")
fi

printf -v remote_command "%q " "${remote_args[@]}"
remote_command="${remote_command% }"

if [[ -n "${BLOG_SSH_PASSWORD:-}" ]]; then
  if ! command -v sshpass >/dev/null 2>&1; then
    echo "sshpass is required when BLOG_SSH_PASSWORD is set." >&2
    exit 1
  fi

  SSHPASS="$BLOG_SSH_PASSWORD" sshpass -eSSHPASS ssh -o StrictHostKeyChecking=no "$BLOG_SERVER" "$remote_command"
else
  ssh "$BLOG_SERVER" "$remote_command"
fi

