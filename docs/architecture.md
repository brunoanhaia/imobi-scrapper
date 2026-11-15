# Architecture

## Layers

```
CLI (typer) / scheduler container
        |
        v
pipeline.run_all -> pipeline.run_source (one session + one run row per source)
        |
        v
sources registry  --type-->  Scraper subclass  --yields-->  RawListing
        |                          |
        |                          +-- HttpClient (rate limit, retries, no WAF retry)
        |                          +-- Capabilities (server_side_price, max_pages, ...)
        v
filter + upsert (listing, price_history, triage default row)
        |
        v
Postgres  <--  sqladmin (/admin)
```

## Key decisions

**Postgres is the source of truth.** Sheets/NocoDB/custom UI are views over the same data, so
switching the UI never touches the collector.

**Triage is a separate table.** `triage` holds `seen`, `interested`, `notes`. The scraper writes
only to `source`, `listing`, `price_history` and `run`. This makes it impossible for a collection
run to erase a user classification.

**Idempotent upsert.** `listing` is unique per `(source_id, external_id)`. A re-run updates
mutable fields, keeps `first_seen_at`, updates `last_seen_at` and appends `price_history` only
when the price actually changed.

**Deactivation, not deletion.** After a successful run, listings of that source that were not
seen are flagged `is_active = false`. A failed run never deactivates anything.

**Scraper types are strategies.** `src/imovel/sources/` is scanned at import time; each module
registers a class under a name. A `source` row selects the type and carries its JSON config,
validated with pydantic before the run starts. Unknown config keys are rejected so typos fail
loudly instead of silently producing empty runs.

**Capabilities drive the generic pipeline.** Price bands, early stop and pagination limits are
declared once per type, not per site.

## Price filtering

1. **Server-side** — ZAP/VivaReal (`precoMinimo`/`precoMaximo`), Imoview
   (`valorde`/`valorate`). Portals cap pagination at ~50 pages, so a price band is often the
   only way to cover the whole catalog.
2. **Ordering + early stop** — when the source sorts by ascending price, stop at the first
   listing above the ceiling.
3. **Local re-check** — always applied. Chaves na Mão silently ignores `precoMinimo`/
   `precoMaximo` (verified: five different URLs returned byte-identical pages), which is exactly
   why layer 3 cannot be optional.

## Run accounting

Each execution writes a `run` row with `fetched`, `kept`, `created`, `updated`, `deactivated`,
`skipped_out_of_range`, `skipped_out_of_scope`, `skipped_other_city`, `skipped_invalid`,
`requests`, `failed_requests` and a status:

| Status | Meaning |
|---|---|
| `ok` | Listings kept, no failed requests |
| `partial` | Some requests failed but data was collected |
| `suspect` | Zero listings kept — likely broken selectors or a WAF block |
| `failed` | Exception before any conclusion; `error` holds the message |

`suspect` is the alarm for silent breakage: a `generic_html` source that starts returning zero
items instead of looking like "no new listings today".

## Schema evolution

Sprint 1 uses `Base.metadata.create_all`. Alembic is planned for phase 2; until then, schema
changes require recreating the database (acceptable while the only data is disposable).

## Non-goals (phase 2)

Browser driver for WAF-protected portals, cross-site dedupe ("also listed on"), Leaflet map,
saved searches, Telegram digest, HTMX front-end, Alembic migrations.
