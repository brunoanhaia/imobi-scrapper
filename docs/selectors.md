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

## VivaReal / ZAP — `zap_group_html`

- Same markup: cards are `[data-cy="rp-property-cd"]`, with fields
  `rp-cardProperty-{price,location,street,propertyArea,bedroomQuantity,bathroomQuantity,parkingSpacesQuantity}-txt`.
- Plain HTTP returns 403 (TLS fingerprint); headless Chromium gets 200 (verified 2026-11-15).
- **Session quirk:** the second navigation in the *same* browser context gets 403. Opening each
  page in a fresh context (new cookies) works, so `BrowserScraper.fetch_html` defaults to
  `fresh=True`. Verified with 2 pages: 55 listings, zero failures.
- Verified selectors: `[data-cy="rp-property-cd"]` cards; fields `rp-cardProperty-price-txt`
  (price + `Cond.` + `IPTU` in the same string), `-location-txt` ("Bairro, Cidade"),
  `-street-txt`, `-propertyArea-txt` ("Tamanho do imóvel160 m²"), `-bedroomQuantity-txt`,
  `-bathroomQuantity-txt`, `-parkingSpacesQuantity-txt`; listing id inside the URL as
  `-id-<digits>`. Two hosts, one parser: only `city_path` differs.
- Each search serves ~50 pages (~1,500 listings), so large catalogs must be split into price
  bands (`precoMinimo`/`precoMaximo`) or by property type; band edges duplicate listings, which
  the unique index absorbs.

## Imovelweb — `navent_html`

- Navent markup: `[data-qa="POSTING_CARD_PRICE"]`, `POSTING_CARD_DESCRIPTION`,
  `POSTING_CARD_FEATURES`, `POSTING_CARD_LOCATION`, `POSTING_CARD_GALLERY`.
- **Session quirk:** same as VivaReal — 403 when reusing the context, 200 with a fresh one
  (tested 2026-11-15: 2 pages, 58 listings, zero failures). Keep the page budget small anyway,
  since Cloudflare starts challenging deeper pages.
- Verified selectors: `[data-qa="POSTING_CARD_PRICE"]`, `POSTING_CARD_DESCRIPTION`,
  `POSTING_CARD_FEATURES` ("232 m² tot.7 ban."), `POSTING_CARD_LOCATION` ("Bairro, Cidade"),
  `POSTING_CARD_GALLERY` (images), listing id as `-<digits>.html` in the URL. Cards have only
  CSS-module classes, so ascend until the ancestor containing GALLERY and DESCRIPTION.

## Kenlo — `kenlo_api`

Agencies hosted by Kenlo (Pecini, among others) are SPAs backed by a public JSON API:

```
GET https://{host}/api/listings/a-venda/rio-claro        -> 12 listings per page
GET https://{host}/api/listings/a-venda/rio-claro?pagina=2
```

- The **city route** matters: `/api/listings/a-venda` returns 1,100 listings across every city the
  agency serves, while `/api/listings/a-venda/rio-claro` returns Rio Claro only (951 on Pecini).
  `/a-venda/sp/rio-claro` also works.
- Response: `{"data": [...], "count": N, "aggs": {...}, "self": "/a-venda/rio-claro"}`.
- No server-side price/type filters: unknown query params (`cidade`, `preco-minimo`,
  `sale_price_min`, `limite`, ...) are ignored and `count` stays 1,100. The pipeline applies the
  price window and type filter locally.
- Fields worth knowing (most numerics arrive as `[value, value]` pairs): `url`,
  `property_full_reference` (listing code), `about_title`, `listing_description`, `sale_price`,
  `retail_price`, `area`, `bedrooms`, `bathrooms`, `garages`, `city`, `state`,
  `neighborhood_display`, `property_type` (`HOUSE`, `APARTMENT`, `LAND`, ...), `photos[]` with
  `picture_full` / `picture_thumb`.
- `property_metreage` reports `SQFT`, but the published area is m² (cross-checked against the
  listing title) — ignore the unit field.
- No lot size in the listing payload; when the description mentions a lot, the prose parser picks
  it up.

## OLX — `olx_html`

- Listing URL: `https://www.olx.com.br/imoveis/venda/estado-sp/rio-claro`, pagination `?o=N`
  (50 per page, all pages respond as of 2026-11-15).
- Cards: `section.olx-adcard`; link `a[data-testid="adcard-link"]` (listing id is the trailing
  number); price `.olx-adcard__price`; condo `.olx-adcard__price-info`; location
  `.olx-adcard__location` — **city first** ("Rio Claro, Zona Central"), unlike the other portals;
  area/bedrooms/bathrooms/parking come from `.olx-adcard__detail` `aria-label`s
  ("50 metros quadrados", "2 quartos", ...).
- Needs a browser (plain HTTP is blocked).

## Tecimob — `tecimob_api` (not implemented yet)

- `https://api-sites.tecimob.com.br/api/properties` with an `x-domain` header identifying the
  site; returns the whole catalog in one call. Photos only exist in the property detail endpoint.

## Probe markers

`imobi probe` looks for: `retornar-imoveis-disponiveis` / `cdn.imoview.com.br` (Imoview),
`api-sites.tecimob.com.br` (Tecimob), `kenlo.com.br` / `/api/listings/` (Kenlo), `fastimob`,
`data-cy="rp-property` (ZAP group), `data-qa="posting_card` (Navent), `"realestatelisting"`
(JSON-LD).

## Politeness

1 request every `RATE_LIMIT_SECONDS` (default 3s, with ±10–30% jitter) per host, 3 retries with
exponential backoff on 408/425/429/5xx, no retries on WAF blocks, per-source `max_pages` and
`max_items_per_run` caps. Kenlo needs ~80 pages for a city catalog, so it is the slowest source
by design; keep the interval at 3s or higher.
