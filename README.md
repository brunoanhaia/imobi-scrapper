# imovel-scrapper

Aggregates **for-sale** residential listings (houses and apartments) in **Rio Claro/SP** from
portals and local agencies into a single Postgres database, with a triage UI to validate the
collection.

Sprint 1 ships:

- two working sources — **Chaves na Mão** (portal) and **Xavier Camargo** (local agency, Imoview);
- a **price window** (default BRL 100,000 – 600,000) applied server-side where possible and
  always re-checked locally;
- a **strategy registry** so new agencies are configuration and new platforms are one file;
- a `sqladmin` UI at `/admin` with filters, triage flags and per-run audit counters;
- a scheduled container that runs the collection every `SCRAPE_INTERVAL_SECONDS`.

## Quick start (local, sqlite)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

imovel seed          # tables + default criteria + starter sources
imovel scrape        # collect
imovel serve         # http://localhost:8000/admin  (admin / admin)
```

Without `DATABASE_URL` the default is `sqlite:///data/imovel.db`. If you already copied `.env`
for the containers, override it for local runs — environment variables win over `.env`:

```bash
DATABASE_URL=sqlite:///data/imovel.db imovel scrape
```

## Quick start (containers)

```bash
cp .env.example .env         # review ADMIN_PASSWORD and the price window
docker compose up -d         # or: podman-compose up -d
docker compose logs -f scraper
```

That starts `db` (Postgres), `app` (web UI on port 8000) and `scraper` (scheduled collector).
Everything is configured through environment variables; see `.env.example`.

## Triage interface

Mobile-first, served by the same FastAPI app on the same port. It shares the admin credentials
and the `session` cookie, so one login covers both.

| Route | Purpose |
|---|---|
| `/novos` | Listings you have not classified yet (20 per page, infinite scroll) |
| `/deck` | One card at a time; each decision is a plain form POST and the server renders the next card |
| `/interessados` · `/talvez` · `/descartados` | Shortlists by decision |
| `/imovel/{id}` | Gallery, facts, price history, "also listed on", description |
| `/img?u=...&w=640` | On-demand image proxy (allowlisted hosts, disk cache) |

Filters (price range, neighborhood, bedrooms, R$/m² ceiling, type, source) are query-string
based, so any filtered view is a shareable URL. Triage buttons write to `triage` and append to
`triage_event`. The deck is **server-rendered**: the card is a `<form>` and the POST answers with a
303 to the next card plus a confirmation banner, so a decision is never shown as saved before the
server confirmed it. `deck-enhance.js` (~100 lines) only translates swipe/arrow keys into
`form.requestSubmit()` — if it fails to load, the buttons still work.

The detail page fetches the listing page **once** on demand (7-day TTL, cached in
`listing_detail`) — no bulk image download, no iframe (portals send `X-Frame-Options: DENY`).

## Dead listings

Agencies on Imoview keep returning properties in their API after they remove the page (sampled:
10 of 10 old Xavier Camargo listings answered `410 Gone`). Two layers keep that out of your way:

- opening a listing fetches its page once; `404`/`410` deactivates it and the card stops
  appearing (a cached failure is retried after 10 minutes, not a week);
- `imovel check-links --limit N` validates a rotating sample of plain-HTTP sources and
  deactivates the dead ones. The scheduler runs it with `--limit 30` after each collection.

Browser-only portals are skipped by the sweep (they answer 403 to plain HTTP).

## CLI

| Command | What it does |
|---|---|
| `imovel seed` | Creates tables, default criteria and starter sources. Idempotent, never overwrites your edits. |
| `imovel scrape [-s SOURCE] [--min-price N] [--max-price N]` | Runs enabled sources and prints per-source counters. |
| `imovel sources` | Lists configured sources with their last run. |
| `imovel types` | Lists registered scraper types and capabilities. |
| `imovel probe URL` | Fingerprints a site and suggests the scraper type + config. |
| `imovel stats` | Quick counters over the database. |
| `imovel serve` | Starts the web UI (`/admin`) + `/healthz`. |

## Search criteria

Stored in the `criteria` table (a single row) and editable in `/admin`. Precedence:

```
CLI flags (--min-price/--max-price)  >  criteria row  >  env defaults  >  100000 / 600000
```

Price filtering happens in three layers: server-side parameters when the source supports them,
price-ascending early stop when available, and a mandatory local re-check before persisting.
Items dropped by the local check are counted as `skipped_out_of_range` in the `run` table — a
high number there means the source ignored the filter.

## Data model

| Table | Contents |
|---|---|
| `source` | Site id, name, scraper `type`, JSON `config`, `enabled` |
| `listing` | Normalized listing, unique per `(source_id, external_id)` |
| `price_history` | One row per price observation (only when the price changes) |
| `triage` | **Your** flags: `seen`, `interested`, `notes` — the scraper never writes here |
| `run` | Audit row per source execution with counters and status |
| `criteria` | Editable search criteria (price window, types, neighborhoods, limits) |

Listings that disappear from a successful run become `is_active = false`; nothing is deleted.

Areas: `area_usable` is the built/private area, `area_land` is the lot size, `area_total` is
kept for sources that expose a single undifferentiated "total area".

## Sources

| Source | Type | Land area | Notes |
|---|---|---|---|
| Chaves na Mão | `json_ld` | no | Portal listing pages expose only one area (`floorSize`). |
| Xavier Camargo | `imoview_api` | yes | Imoview API returns built area (`areaprincipal`) and lot (`arealote`). |
| Pecini | `kenlo_api` | no | Kenlo API exposes built area only; lot size only when written in the description. |
| VivaReal | `zap_group_html` | no | Needs Chromium; 30 per page, `?pagina=N`. |
| ZAP Imóveis | `zap_group_html` | no | Same OLX Group markup as VivaReal. |
| OLX Imóveis | `olx_html` | no | Own markup; 50 per page, `?o=N`. |
| Imovelweb | `navent_html` | no | Cloudflare; needs a fresh browser context per page. |

Browser-driven sources need Chromium (`playwright install chromium`, already in the image). They
are slower by design: each page is a real navigation plus the 3s per-host interval.

Known limitation: VivaReal, ZAP, OLX and Imovelweb block plain HTTP (403 / WAF) and need a
browser driver — that is phase 2, together with cross-site deduplication and a map view.

## Adding a source

See [docs/adding-a-source.md](docs/adding-a-source.md). Short version:

```bash
imovel probe https://www.some-agency.com.br/venda
# review the suggested config in /admin -> Source -> create / edit
```

Adding an agency on a known platform is a `source` row. Adding a platform is one module with
`@register("type_name")` in `src/imovel/sources/`.

## Schema migrations

Alembic owns the schema. The app calls `ensure_schema()` on startup, which:

- upgrades a versioned database (`alembic_version` present) to `head`;
- creates the current schema and stamps it for a fresh or legacy database.

Manual commands (same behaviour, explicit):

```bash
alembic upgrade head
alembic revision --autogenerate -m "describe the change"
```

`alembic.ini`/`migrations/` live in the repo and in the image, so a new revision deploys with
the next build — no manual `ALTER TABLE`.

## Development

```bash
make test           # offline contract tests (network tests are marked `live`)
make lint           # ruff
make build          # rebuild the image
make up / make down # compose stack
make refresh        # recreate containers on the new image, then prune the old one
make clean          # drop dangling images + build cache
```

**Disk hygiene:** every rebuild leaves the previous image behind (~1.8 GB each). Run `make clean`
after building; it only prunes *images and build cache*. Collected data is never touched — it
lives in the Postgres volume (`data/pgdata`) and in the database itself.

Architecture and design decisions: [docs/architecture.md](docs/architecture.md).
Per-source technical notes (endpoints, selectors, quirks): [docs/selectors.md](docs/selectors.md).

## Portability

The image is stateless; only `./data/pgdata` persists. All configuration is environment
driven, so moving from the test machine to a production host is `git clone` + `cp .env.example
.env` + `docker compose up -d`. Works with `docker` and `podman` (rootless) alike.

The image ships Chromium and its shared libraries (~1.3 GB) because four sources need a real
browser. On a memory-constrained host, budget ~1 GB of RAM: one browser instance runs at a time,
inside the `scraper` container.
