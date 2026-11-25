# Adding a source

Three levels, cheapest first. Always start with `probe`.

```bash
imobi probe https://www.agency.com.br/venda
```

It reads one page and reports the markers that identify the platform, then prints a ready-to-edit
`source` config.

## Level 1 — agency on a known platform (no code)

Insert a `source` row (in `/admin` → Source → Create, or via `imobi seed` defaults):

```json
{
  "id": "village",
  "name": "Village Negócios Imobiliários",
  "type": "kenlo_api",
  "enabled": false,
  "config": { "host": "https://villageimobiliaria.com.br", "max_pages": 20 }
}
```

Keep `enabled: false` until the first run looks right, then enable it.

| `type` | Config required | Notes |
|---|---|---|
| `json_ld` | `host`, `sale_paths[]` | One sale path per property type; pagination via `page_param` (default `pg`) |
| `imoview_api` | `host` | Optional `city`, `city_code` (server-side city filter) |
| `generic_html` | `host`, `sale_paths[]`, `selectors.card` | See level 2 |
| `kenlo_api` | `host`, `city_path` | API `/api/listings/a-venda/{city_path}?pagina=N`; no server-side filters |
| `tecimob_api` | `host` | API `api-sites.tecimob.com.br` with `x-domain` header |
| `zap_group_html` | `host`, `city_path` | VivaReal/ZAP; needs a browser driver (phase 2) |
| `navent_html` | `host`, `city_slug` | Imovelweb/Casamineira; needs a browser driver (phase 2) |

## Level 2 — bespoke HTML (configuration only)

`generic_html` parses cards from CSS selectors. Example:

```json
{
  "type": "generic_html",
  "config": {
    "host": "https://www.agency.com.br",
    "sale_paths": ["venda/imovel/rio-claro"],
    "page_param": "pagina",
    "items_per_page": 12,
    "encoding": "utf-8",
    "selectors": {
      "card": ".property-card",
      "title": ".card-title",
      "price": ".card-price",
      "link": "a",
      "photo": "img",
      "neighborhood": ".card-neighborhood",
      "area": ".card-area",
      "area_land": ".card-land-area",
      "bedrooms": ".card-bedrooms"
    }
  }
}
```

Use `"encoding": "iso-8859-1"` for legacy sites (Estrutura Imóveis, for example). If the site
needs JavaScript, iframes or login, it belongs to level 3.

## Level 3 — new platform or unusual site (one module)

Create `src/imobi/sources/my_platform.py`:

```python
from . import register
from .base import Capabilities, RawListing, Scraper, SourceConfig

class MyConfig(SourceConfig):
    sale_paths: list[str] = []

@register("my_platform")
class MyScraper(Scraper):
    capabilities = Capabilities(server_side_price=True, price_params={"min": "min", "max": "max"})
    config_model = MyConfig

    @property
    def my_config(self) -> MyConfig:
        assert isinstance(self.spec.config, MyConfig)
        return self.spec.config

    def fetch(self):
        for path in self.my_config.sale_paths:
            response = self.client.get(f"{self.config.host}/{path}")
            for item in response.json().get("items", []):
                yield RawListing(...)
```

Rules:

- respect `self.max_pages` and `self.min_price` / `self.max_price`;
- filter by city inside the scraper when the source returns neighbors (the pipeline does not know
  the site's city semantics);
- never raise for a single bad listing — skip it and continue;
- declare honest capabilities; the pipeline relies on them.

## Checklist before enabling

1. `imobi probe` suggests the type and the fields match reality.
2. A fixture is saved in `tests/fixtures/` and a parsing test asserts the mapping (price,
   neighborhood, areas, bedrooms, photos).
3. `imobi scrape -s <id>` reports `status=ok`, `kept > 0`, and a plausible
   `skipped_out_of_range` (zero is suspicious when the source has a big catalog).
4. `/admin` → Run shows the row; `/admin` → Listing filters by source.
5. Only then set `enabled: true`.

## Adding a source without deploying

`source` rows live in the database, so an agency on a known platform needs no rebuild. A new
*platform* needs the module above, hence a new image — expected and intentional.
