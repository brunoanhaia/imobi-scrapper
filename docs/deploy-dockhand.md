# Deploy on Dockhand behind Traefik and TinyAuth

The production stack is `deploy.compose.yaml`. Dockhand builds the image and runs three
containers: `db` (Postgres 16), `app` (web UI + backup page) and `scraper` (scheduled collector).
The database is never published on the host; only Traefik reaches the app.

## Prerequisites

- Docker Engine and Dockhand on the target host.
- An external Traefik instance with a Docker network (default name `traefik`) and a certificate
  resolver (default name `letsencrypt`).
- A TinyAuth forward-auth middleware reachable by Traefik (default reference `tinyauth@file`).
- A DNS record for the chosen domain pointing at the Traefik entrypoint.
- The Git repository reachable by Dockhand (the repository is public today).

## Create the stack in Dockhand

**Stacks → New stack → Git**:

| Field | Value |
|---|---|
| Repository | `https://github.com/brunoanhaia/imobi-scrapper.git` |
| Branch | `main` |
| Compose file path | `deploy.compose.yaml` |
| Context directory | `.` (repository root) |
| Build images on deploy | **on** |
| Auto-sync | optional (schedule or webhook) |

### Secrets (Dockhand Secrets or stack environment)

| Variable | Notes |
|---|---|
| `POSTGRES_PASSWORD` | strong random value |
| `DATABASE_URL` | `postgresql+psycopg://imovel:<mesma senha>@db:5432/imovel` (percent-encode `@`, `:` and `/` in the password, or use an alphanumeric password) |
| `ADMIN_PASSWORD` | strong random value |
| `ADMIN_SECRET_KEY` | strong random value (rotating it logs everyone out) |

### Non-secret environment

Copy `deploy/.env.example` and adjust:

- `POSTGRES_USER`, `POSTGRES_DB`;
- `ADMIN_USER`;
- `APP_DOMAIN` (hostname only, e.g. `imoveis.example.com`);
- `TRAEFIK_NETWORK`, `TRAEFIK_ENTRYPOINT`, `TRAEFIK_CERT_RESOLVER`, `TINYAUTH_MIDDLEWARE`;
- criteria, scrape interval and backup limits.

The Traefik labels are already in `deploy.compose.yaml`:

```yaml
traefik.enable: "true"
traefik.docker.network: traefik
traefik.http.routers.imovel.rule: Host(`imoveis.example.com`)
traefik.http.routers.imovel.entrypoints: websecure
traefik.http.routers.imovel.tls.certresolver: letsencrypt
traefik.http.routers.imovel.middlewares: tinyauth@file
traefik.http.services.imovel.loadbalancer.server.port: "8000"
```

If your TinyAuth middleware is defined with another name, change `TINYAUTH_MIDDLEWARE` (for
example `tinyauth@docker`). A file-provider middleware looks like:

```yaml
http:
  middlewares:
    tinyauth:
      forwardAuth:
        address: http://tinyauth:3000/api/auth
        trustForwardHeader: true
        authResponseHeaders: ["Remote-User", "Remote-Groups", "Remote-Email", "Remote-Name"]
```

## First deploy

1. Deploy the stack. `db` becomes healthy, `app` runs the Alembic migrations, `scraper` seeds the
   criteria/sources and starts the first collection.
2. Open `https://<APP_DOMAIN>/healthz` (through TinyAuth) — expect `{"status":"ok"}`.
3. Log in with `ADMIN_USER` / `ADMIN_PASSWORD`.
4. Check `/novos` and `/deck`, then `/backup` (see [backup-restore.md](backup-restore.md)).
5. Optionally enable Dockhand auto-sync so a push to `main` rebuilds and redeploys the stack.

## Update / rollback

- **Update**: push to `main`, then Dockhand *Deploy* (or let auto-sync run). The image is rebuilt
  and the stack redeployed; the named volumes keep the data.
- **Rollback**: deploy an earlier commit from the same Git stack (Dockhand keeps the deploy
  history); if a migration ran, restore a backup downloaded before the update.

## Volumes

| Volume | Contents | Backup |
|---|---|---|
| `imovel_pgdata` | Postgres data | use `/backup` (dump download) |
| `imovel_state` | image cache, maintenance flag, safety dumps | not required |

## Network and security notes

- `app` and `db` publish **no** host ports. The only entry point is Traefik, gated by TinyAuth.
- `SESSION_HTTPS_ONLY=true` marks the session cookie `Secure`; `ALLOWED_HOSTS` is the public
  hostname.
- `FORWARDED_ALLOW_IPS="*"` is safe here because the app is not reachable outside the Docker
  network that contains Traefik.
- The app always accepts the `localhost`/`127.0.0.1` Host headers so the container healthcheck
  works; `ALLOWED_HOSTS` still restricts the public domain.
- Rotate the four secrets before the first deploy; never commit a real `.env`.
