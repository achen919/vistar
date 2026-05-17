# Vistar Blog

Hugo blog deployed at <https://shcxyz.site/>.

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

