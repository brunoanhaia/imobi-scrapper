# Changelog

## [0.2.0](https://github.com/brunoanhaia/imobi-scrapper/compare/v0.1.0...v0.2.0) (2026-11-23)


### Features

* **admin:** local-time and BRL formatting, drop zero areas at ingest ([25c1f05](https://github.com/brunoanhaia/imobi-scrapper/commit/25c1f055ec2263a0d5174605c6e3cd1bb8b0aed6))
* **backup:** download and restore the database from the web UI ([bb0dc7b](https://github.com/brunoanhaia/imobi-scrapper/commit/bb0dc7b6bd6d5cd81b6efff49812988c46e8a722))
* **backup:** pg_dump backups, rotation and guarded restore ([1b7ddd1](https://github.com/brunoanhaia/imobi-scrapper/commit/1b7ddd164504b2378a3ad999bd791b32d2ae496f))
* **backup:** run pg_dump in the postgres image ([ff4f1b4](https://github.com/brunoanhaia/imobi-scrapper/commit/ff4f1b4016648bda4bf41ba740ecafc2a821e232))
* **ci:** add GHCR container build, Trivy scan, and release-please versioning ([1b0247d](https://github.com/brunoanhaia/imobi-scrapper/commit/1b0247dc8a3f509da0ed9b94cff03edc80aee756))
* **cli:** duplicates and per-source summary reports ([5da6ab5](https://github.com/brunoanhaia/imobi-scrapper/commit/5da6ab5d01d0558d77afa6b5eb4c17fafe7c7969))
* **db:** alembic migrations with automatic bootstrap ([8d604f8](https://github.com/brunoanhaia/imobi-scrapper/commit/8d604f82b981652f67545d0f4ad5c342203e8daa))
* **deck:** server-rendered decisions with a thin gesture layer ([d51c9df](https://github.com/brunoanhaia/imobi-scrapper/commit/d51c9dfdc6149e0647ce2a896555f77a1f65abf7))
* **deck:** Shift+ArrowUp decides, flash as overlay with undo ([76de1a7](https://github.com/brunoanhaia/imobi-scrapper/commit/76de1a70a19f0c767c7e9423862fd1027f855e16))
* **deck:** swipe deck with icons, large photos and group-aware queue ([28a409f](https://github.com/brunoanhaia/imobi-scrapper/commit/28a409f01d712b0cc318771fbf6669019cabefc0))
* **dedupe:** conservative cross-source grouping key ([89e8a89](https://github.com/brunoanhaia/imobi-scrapper/commit/89e8a897b79ad8a61afe704124a0dd9b9a84c36a))
* **export:** full CSV/JSON export with every field ([077072e](https://github.com/brunoanhaia/imobi-scrapper/commit/077072ed707f1a2895d3cdb7e5a89207aca648b5))
* **images:** upscale the CDN variant when the URL already has a dimension ([b75e4f7](https://github.com/brunoanhaia/imobi-scrapper/commit/b75e4f7838e3319c8568983f042ddf542ffc9d40))
* **seed:** enable Pecini by default ([0cae6f2](https://github.com/brunoanhaia/imobi-scrapper/commit/0cae6f25bd04e31f668ad3d439124d2ec90e13a1))
* **sources:** browser transport and the four big portals ([e11afaf](https://github.com/brunoanhaia/imobi-scrapper/commit/e11afafb7603795c084e139c92849d408baf69bf))
* **sources:** json_ld and imoview_api scrapers with area/land area support ([b2f2ebc](https://github.com/brunoanhaia/imobi-scrapper/commit/b2f2ebc7e72bf83bc534ba867835230006387c5e))
* **sources:** kenlo_api scraper (Pecini) and calmer request rhythm ([98c5700](https://github.com/brunoanhaia/imobi-scrapper/commit/98c5700a8ccc9fbee64c927cdad40d8512dd0eae))
* **ui:** Enter and card tap open the listing; e2e sandbox database ([b7e187a](https://github.com/brunoanhaia/imobi-scrapper/commit/b7e187abea8884c5376db19c119c9ab93a2f8952))
* **ui:** finish the icon refactor and fix two layout bugs ([856d6cd](https://github.com/brunoanhaia/imobi-scrapper/commit/856d6cd07a6bd9e64fab2c25db17248c644d170d))
* **web:** mobile-first triage interface with image proxy and on-demand detail ([463358b](https://github.com/brunoanhaia/imobi-scrapper/commit/463358bc5cd0b364bb5b8cc3c94ed7beeda34082))
* **web:** survive an unstable connection ([30ca305](https://github.com/brunoanhaia/imobi-scrapper/commit/30ca30522dd966417a20f2b011e814a552ff06de))


### Bug Fixes

* **auth:** use constant-time credential comparison ([8c81db5](https://github.com/brunoanhaia/imobi-scrapper/commit/8c81db544d1cae971a1c1ab3c20444295f738e07))
* **browser:** fresh context per navigation for WAF portals ([b4c9543](https://github.com/brunoanhaia/imobi-scrapper/commit/b4c9543561eb75fd52df4949f0dde6b23c25418f))
* **deck:** Enter opens the original ad, tap opens our detail ([56ee519](https://github.com/brunoanhaia/imobi-scrapper/commit/56ee519167c35b7d18e7d62220ce98a4b5995c41))
* **deck:** validate the top card before rendering it ([debfcfc](https://github.com/brunoanhaia/imobi-scrapper/commit/debfcfc4a1ce4b0ff75be84edbf32cd5aa2096d9))
* **export:** include triage in the back-office export ([2163476](https://github.com/brunoanhaia/imobi-scrapper/commit/2163476c586daa1ff7b0fca775f33ac984920cc8))
* **images:** never rewrite CDN URLs, lighter Kenlo thumbs ([4214f4d](https://github.com/brunoanhaia/imobi-scrapper/commit/4214f4d80732f0ae69061dddbd3e432af9900ff6))
* **js:** resolve the gesture decision without dynamic object indexing ([1da2b0a](https://github.com/brunoanhaia/imobi-scrapper/commit/1da2b0a4f69b3647291d8e9aac8d11324001cc84))
* **links:** deactivate listings whose page is gone ([256a652](https://github.com/brunoanhaia/imobi-scrapper/commit/256a6528d5118e318df6d3c785e6ff9687a3777c))
* **pipeline:** per-source page budget honours the criteria ceiling ([06f1c9e](https://github.com/brunoanhaia/imobi-scrapper/commit/06f1c9ed34a40990652d7dfd5c50a3482cfb927d))
* **quality:** clear the last Sonar findings ([9ef4d5d](https://github.com/brunoanhaia/imobi-scrapper/commit/9ef4d5d2dd21236e2871470ed511ea301dbec23d))
* **quality:** clear the remaining Sonar gate blockers ([292d2a7](https://github.com/brunoanhaia/imobi-scrapper/commit/292d2a750cad5f76a62827313998b65307cfd07b))
* **quality:** close the remaining codacy findings ([64e06c9](https://github.com/brunoanhaia/imobi-scrapper/commit/64e06c9a05757882cf15779870588d4b570a45e7))
* **quality:** codacy round ([20a0b23](https://github.com/brunoanhaia/imobi-scrapper/commit/20a0b23fc424c248323e5bd8533ac123c179fc9d))
* **quality:** narrow with cast instead of NOSONAR; drop the prospector override ([ca270c7](https://github.com/brunoanhaia/imobi-scrapper/commit/ca270c73c84736a347cecfada7422fe5d0e215d3))
* **quality:** second analyzer round ([da21f34](https://github.com/brunoanhaia/imobi-scrapper/commit/da21f34b5aec91434b10a40f88c6a0a5b0d552da))


### Documentation

* dead listings, link sweep and local dev workflow ([717cff5](https://github.com/brunoanhaia/imobi-scrapper/commit/717cff5f224bdabb9f288f54086b1129c6d99dbd))
* Kenlo findings, local-run note, probe sale-path fix ([6be07ac](https://github.com/brunoanhaia/imobi-scrapper/commit/6be07aca826e3e3175d47ae9f61f9de677c598b6))
* triage interface, image proxy and web layer architecture ([8a8a8a1](https://github.com/brunoanhaia/imobi-scrapper/commit/8a8a8a17d2303f02b8008cefcff2337869b8093c))
