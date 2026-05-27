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

The generated `public/` directory is intentionally ignored. The server builds and serves `/www/wwwroot/blog/public`.

## Automatic Deployment

GitHub Actions deploys every push to `main`.

The workflow checks out the repository with the PaperMod submodule, syncs the source to `/www/wwwroot/blog`, runs `hugo --gc --minify` on the server, and verifies `https://shcxyz.site/`.

Required repository Secrets:

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | Server IP or hostname |
| `DEPLOY_USER` | SSH user, for example `root` |
| `DEPLOY_PASSWORD` | SSH password |
| `DEPLOY_PATH` | Optional, defaults to `/www/wwwroot/blog` |

## Server Management

Use the helper script without committing server credentials:

```bash
export BLOG_SERVER=root@62.234.29.88
export BLOG_SSH_PASSWORD='your-server-password'
./scripts/blog.sh build
```

If `BLOG_SSH_PASSWORD` is not set, the script falls back to normal SSH authentication.

## SSL

Nginx is configured to serve:

- `https://shcxyz.site/`
- `https://www.shcxyz.site/`

The live certificate and private key are stored on the server at `/etc/nginx/ssl/shcxyz.site/`. Do not commit certificate private keys, panel credentials, or SSH passwords to this repository.

The deployed nginx template is tracked at `deploy/nginx/shcxyz.site.conf`.
The template also applies per-client Nginx request and connection limits.
