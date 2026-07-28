# Vistar Blog

Hugo blog deployed at <https://shcxyz.site/>.

The site title is `阿辰的博客`. Articles are organized under five configured categories:

- `技术-后端开发`
- `技术-agent`
- `技术-llm`
- `随笔-胡思乱想`
- `随笔-如何搞钱`

## Local Development

```bash
git submodule update --init --recursive
hugo server -D
```

Build the static site:

```bash
hugo --minify
```

The generated `public/` directory is intentionally ignored. Production builds
into a release directory and atomically switches
`/www/wwwroot/blog/public` to that release through a symbolic link.

## Automatic Deployment

GitHub Actions deploys every push to `main`.

The workflow validates the admin module graph and tests, saves a recoverable
source snapshot, syncs `/www/wwwroot/blog`, builds Hugo into a versioned public
release, and switches the public symlink only after the admin service is
healthy. Failed production checks restore both the previous static release and
the previous server source before restarting the service.

Required repository Secrets:

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | Server IP or hostname |
| `DEPLOY_USER` | SSH user, for example `root` |
| `DEPLOY_PASSWORD` | SSH password |
| `DEPLOY_HOST_KEY` | Verified `known_hosts` line for the production server |
| `DEPLOY_PATH` | Optional, defaults to `/www/wwwroot/blog` |

## Server Management

Use the helper script without committing server credentials:

```bash
export BLOG_SERVER=root@62.234.29.88
export BLOG_SSH_PASSWORD='your-server-password'
./scripts/blog.sh build
```

If `BLOG_SSH_PASSWORD` is not set, the script falls back to normal SSH authentication.

## Blog management console

The management console lives at `/admin/`. It uses an application login and a
short-lived signed session cookie rather than browser-level HTTP Basic Auth.
The API listens only on `127.0.0.1:18080` and also enforces its own
authentication, CSRF token, same-origin checks, login throttling, and audit log.

The console is split into task-focused routes:

- `/admin/` - overview, recent posts, and traffic summary.
- `/admin/articles/` - searchable article list with create, edit, draft, and delete actions.
- `/admin/articles/new/` - focused Markdown authoring page.
- `/admin/categories/` - category CRUD and persisted drag ordering.
- `/admin/analytics/` - PV/UV trends, popular pages, and referring sites.

Article editing and category editing are independent. Category responses and
article details include versions, so a stale browser tab receives a conflict
instead of silently overwriting newer work. Category names use a stable ID;
renaming a category updates article references. A category that is still in use
must be migrated before it can be deleted.

The backend writes `content/posts/*.md` and category configuration in a
dedicated checkout, validates a complete Hugo build, commits, and pushes to
GitHub `main`. GitHub Actions remains the deployment path and the repository
remains the source of truth. Failed validation is rolled back before anything
is committed. If GitHub is temporarily unreachable after a local commit, the
API returns `publish_pending` and blocks further content mutations so the same
create, update, or delete is not accidentally repeated. Authenticated clients
can inspect `GET /admin/api/publish/status` and retry only the push with
`POST /admin/api/publish/retry` (the retry request requires the normal CSRF
header).

Traffic data is aggregated from the local Nginx access log. PV excludes admin,
API, static-asset, failed, and known bot requests. UV is a keyed HMAC of IP and
user agent; raw visitor identifiers are never returned or persisted by the
admin service. Available history follows the server's access-log retention and
the configured read limit. The public footer uses the same first-party summary
endpoint and no longer runs third-party analytics JavaScript.

Server setup files:

- `static/admin/` - browser editor.
- `tools/blog_admin_server.py` - localhost-only publish API.
- `deploy/systemd/blog-admin.service` - systemd unit.
- `deploy/install-blog-admin.sh` - server installer.
- `deploy/blog-admin.env.example` - environment template.

Do not commit admin passwords, SSH private keys, or unverified host keys. The
installer creates a dedicated, non-login `blog-admin` system user, stores its
deploy key under `/var/lib/blog-admin/.ssh/`, hashes the login password with
PBKDF2-SHA256, and generates separate random session and analytics secrets.

Before cloning over SSH, prepare a dedicated GitHub `known_hosts` file. One
safe workflow is to collect a candidate key, print its fingerprint, compare it
out-of-band with
[GitHub's published SSH fingerprints](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints),
and install the file only after it matches:

```bash
install -d -m 700 /root/.ssh
ssh-keyscan -t ed25519 github.com > /root/.ssh/github-known-hosts.candidate
ssh-keygen -lf /root/.ssh/github-known-hosts.candidate
# Compare the displayed fingerprint with GitHub's official page first.
install -m 600 \
  /root/.ssh/github-known-hosts.candidate \
  /root/.ssh/github-known-hosts
```

`ssh-keyscan` proves only what the current network returned; its output is not
trusted until the fingerprint has been independently verified.

On a new server, run the installer once without a password. It creates the
deploy key, prints the public half, and deliberately exits with status `2`
before cloning or changing the running service:

```bash
cd /www/wwwroot/blog
./deploy/install-blog-admin.sh
```

Add `/var/lib/blog-admin/.ssh/vistar_blog_admin_ed25519.pub` to this repository
as a writable deploy key. Then rerun the installer with the verified host-key
file and an interactively entered admin password, so the password is not saved
in shell history:

```bash
cd /www/wwwroot/blog
read -rsp 'Blog admin password: ' BLOG_ADMIN_PASSWORD
printf '\n'
export BLOG_ADMIN_PASSWORD
BLOG_ADMIN_USER='admin' \
  BLOG_ADMIN_GITHUB_KNOWN_HOSTS_FILE='/root/.ssh/github-known-hosts' \
  ./deploy/install-blog-admin.sh
unset BLOG_ADMIN_PASSWORD
```

An upgrade that already has an authorized deploy key can use the second command
directly. The installer rejects symbolic links, broad system directories, and
unexpected source, state, key, log, or deployment paths before changing
ownership or permissions.

The installer requires a pre-verified `known_hosts` entry for `github.com`; it
does not trust an `ssh-keyscan` result automatically. By default it reads
`/root/.ssh/known_hosts`, or you can point
`BLOG_ADMIN_GITHUB_KNOWN_HOSTS_FILE` at another verified file. If the Nginx log
group cannot be detected, set `BLOG_ADMIN_LOG_GROUP` explicitly (for example
`www`, `www-data`, or `nginx`) and configure log rotation to recreate the access
log with that readable group.

For an upgrade from the old Basic Auth console:

1. Run the installer to create `/etc/blog-admin.env` and the restricted service user.
2. Replace the active site vhost with `deploy/nginx/shcxyz.site.conf`.
3. Run `nginx -t`, reload Nginx, and verify login plus logout.
4. Remove the old `.htpasswd-blog-admin` after verification.

The installer deliberately does not overwrite a hosting panel's active Nginx
vhost. It always initializes the management checkout's own PaperMod submodule
once. `BLOG_ADMIN_UPDATE_SUBMODULES=0` only skips repeated submodule updates
during normal content synchronization.

## SSL

Nginx is configured to serve:

- `https://shcxyz.site/`
- `https://www.shcxyz.site/`

The live certificate and private key are stored on the server at `/etc/nginx/ssl/shcxyz.site/`. Do not commit certificate private keys, panel credentials, or SSH passwords to this repository.

The deployed nginx template is tracked at `deploy/nginx/shcxyz.site.conf`.
The template also applies per-client Nginx request and connection limits.
