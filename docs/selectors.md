# Source technical notes

Everything below was verified against the live sites on 2026-11-15 (Rio Claro/SP) or comes from
the reference project [`juninmd/imoveis-franca`](https://github.com/juninmd/imoveis-franca)
(MIT badge, no LICENSE file in the repo — used as conceptual reference only, code reimplemented
in Python).

## Chaves na Mão — `json_ld`

- Listing URL: `https://www.chavesnamao.com.br/{tipo}-a-venda/sp-rio-claro/`
  (`casas`, `apartamentos`, `terrenos`, `imoveis-comerciais`, ...).
- Pagination: `?pg=N`. Page 2 onwards returns different listings (verified). Around page 14 for
  houses the page stops returning JSON-LD — treat that as the end of the list.
- Data source: a single `<script type="application/ld+json">` with `@type: RealEstateListing`:

```
offers.numberOfItems            -> total listings for that path
offers.itemListElement[]        -> page items (30 per page)
  .url                          -> listing URL, external id after "/id-"
  .price                        -> plain number string ("126926")
  .name                         -> title (contains area, bedrooms, neighborhood)
  .description                  -> prose, sometimes mentions the lot size
  .itemOffered.@type            -> SingleFamilyResidence / Apartment
  .itemOffered.numberOfBedrooms, numberOfBathroomsTotal, numberOfParkingSpaces
  .itemOffered.floorSize.unitText  -> "125m²"  (only ONE area; no lot size)
  .itemOffered.address.addressLocality  -> neighborhood
  .itemOffered.address.addressRegion    -> "Rio Claro, SP"  (city filter)
  .offeredBy.name               -> advertiser
```

- **Quirk:** `precoMinimo` / `precoMaximo` are silently ignored. Five URLs with different price
  filters returned byte-identical pages. Hence the mandatory local price filter.
- **Quirk:** responses are brotli-encoded; without a brotli decoder the HTML arrives as garbage
  (this cost us the first run).
- **Limitation:** no lot size on the listing page, and the detail page has no structured lot size
  either — only prose. `area_land` therefore comes from description text when present.

## Imoview — `imoview_api`

Agencies hosted by Imoview (Xavier Camargo, among others) render search results client-side and
fetch:

```
POST https://{host}/retornar-imoveis-disponiveis
Content-Type: application/x-www-form-urlencoded
X-Requested-With: XMLHttpRequest

finalidade=venda&valorde=100000&valorate=600000&codigocidade=2&numeropagina=1&numeroregistros=20
&ordenacao=dataatualizacaodesc&... (the payload has ~40 fields)
```

- The endpoint returns HTTP 500 when required fields are missing, so the scraper mirrors the
  site's own payload (see `_base_form` in `sources/imoview_api.py`).
- Response: `{"quantidade": N, "lista": [...]}`.
- Server-side price window works (`valorde` / `valorate`) and `codigocidade` filters by city
  (Rio Claro = `2` for Xavier Camargo; verify per site).
- Useful fields: `codigo`, `codigoauxiliar`, `titulo`, `tipo`, `valor`, `valorcondominio`,
  `bairro`, `cidade`, `numeroquartos`, `numerobanhos`, `numerovagas`,
  `areaprincipal` (built area), `areainterna`, `areaexterna`, `arealote` (lot size),
  `url_amigavel`, `urlfotoprincipalp`, `fotos[].urlp`.
- Public listing URL: `https://{host}/imovel/{url_amigavel}/{codigo}`.

## VivaReal / ZAP — `zap_group_html` (phase 2)

- Same markup: cards are `[data-cy="rp-property-cd"]`, with fields
  `rp-cardProperty-{price,location,street,propertyArea,bedroomQuantity,bathroomQuantity,parkingSpacesQuantity}-txt`.
- Plain HTTP returns 403 (TLS fingerprint); a stealth browser passes.
- Each search serves ~50 pages (~1,500 listings), so large catalogs must be split into price
  bands (`precoMinimo`/`precoMaximo`) or by property type; band edges duplicate listings, which
  the unique index absorbs.

## Imovelweb — `navent_html` (phase 2)

- Navent markup: `[data-qa="POSTING_CARD_PRICE"]`, `POSTING_CARD_DESCRIPTION`,
  `POSTING_CARD_FEATURES`, `POSTING_CARD_LOCATION`, `POSTING_CARD_GALLERY`.
- Cloudflare starts challenging around page 5 even in a headless browser; ~4 pages per listing
  type, so split by type (`casas-venda-rio-claro-sp.html`, `...-pagina-2.html`).

## Kenlo — `kenlo_api` (not implemented yet)

- SPA backed by `GET {host}/api/listings/{a-venda|para-alugar}?pagina=N` returning JSON, 12 per
  page. No browser needed.

## Tecimob — `tecimob_api` (not implemented yet)

- `https://api-sites.tecimob.com.br/api/properties` with an `x-domain` header identifying the
  site; returns the whole catalog in one call. Photos only exist in the property detail endpoint.

## Probe markers

`imovel probe` looks for: `retornar-imoveis-disponiveis` / `cdn.imoview.com.br` (Imoview),
`api-sites.tecimob.com.br` (Tecimob), `kenlo.com.br` / `/api/listings/` (Kenlo), `fastimob`,
`data-cy="rp-property` (ZAP group), `data-qa="posting_card` (Navent), `"realestatelisting"`
(JSON-LD).

## Politeness

1 request every `RATE_LIMIT_SECONDS` (default 2s) per host, 3 retries with exponential backoff
on 408/425/429/5xx, no retries on WAF blocks, per-source `max_pages` and
`max_items_per_run` caps.
