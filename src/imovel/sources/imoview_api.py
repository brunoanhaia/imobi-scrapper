"""``imoview_api`` scraper type: agencies hosted by Imoview.

Imoview-backed sites render the search page with JavaScript and fetch results from
``POST /retornar-imoveis-disponiveis`` with a large form payload. The backend replies with
``{"quantidade": N, "lista": [...]}`` and accepts a server-side price window
(``valorde``/``valorate``), which is exactly what we want to avoid crawling the whole catalog.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Any, cast

from ..normalize import (
    clean_text,
    guess_property_type,
    parse_area,
    parse_decimal,
    parse_int,
    same_city,
)
from . import register
from .base import Capabilities, RawListing, Scraper, SourceConfig

PAGE_SIZE = 20


def _base_form() -> dict[str, str | int]:
    """Mirror of the payload the site itself posts; omitting fields makes the backend 500."""
    return {
        "finalidade": "venda",
        "codigounidade": "",
        "codigocondominio": 0,
        "codigoproprietario": 0,
        "codigocaptador": 0,
        "codigosimovei": 0,
        "codigocidade": 0,
        "codigoregiao": 0,
        "bairros[0][cidade]": "",
        "bairros[0][codigo]": "",
        "bairros[0][estado]": "",
        "bairros[0][estadoUrl]": "",
        "bairros[0][nome]": "Todos",
        "bairros[0][nomeUrl]": "todos-os-bairros",
        "bairros[0][regiao]": "",
        "endereco": "",
        "edificio": "",
        "numeroquartos": 0,
        "numerovagas": 0,
        "numerobanhos": 0,
        "numerosuite": 0,
        "numerovaranda": 0,
        "numeroelevador": 0,
        "valorde": 0,
        "valorate": 0,
        "areade": 0,
        "areaate": 0,
        "areaexternade": 0,
        "areaexternaate": 0,
        "destaque": 0,
        "opcaoimovel[codigo]": 0,
        "opcaoimovel[nome]": "",
        "opcaoimovel[nomeUrl]": "todas-as-opcoes",
        "codigoOpcaoimovel": 0,
        "numeropagina": 1,
        "numeroregistros": PAGE_SIZE,
        "ordenacao": "dataatualizacaodesc",
        "condominio[codigo]": 0,
        "condominio[nome]": "",
        "condominio[nomeUrl]": "todos-os-condominios",
        "retornomapaapp": "false",
        "codigoempreendimentomae": "",
    }


class ImoviewConfig(SourceConfig):
    city: str = "Rio Claro"
    city_code: int = 0
    ordering: str = "dataatualizacaodesc"


@register("imoview_api")
class ImoviewScraper(Scraper):
    capabilities = Capabilities(
        server_side_price=True,
        price_params={"min": "valorde", "max": "valorate"},
        items_per_page=PAGE_SIZE,
    )
    config_model = ImoviewConfig

    @property
    def imoview_config(self) -> ImoviewConfig:
        return cast(ImoviewConfig, self.spec.config)

    @property
    def endpoint(self) -> str:
        return f"{self.config.host.rstrip('/')}/retornar-imoveis-disponiveis"

    def _form(self, page: int) -> dict[str, str | int]:
        form = _base_form()
        form.update(
            {
                "valorde": int(self.min_price),
                "valorate": int(self.max_price),
                "numeropagina": page,
                "numeroregistros": PAGE_SIZE,
                "ordenacao": self.imoview_config.ordering,
                "codigocidade": self.imoview_config.city_code,
            }
        )
        return form

    def fetch(self) -> Iterator[RawListing]:
        total: int | None = None
        for page in range(1, self.max_pages + 1):
            response = self.client.post(
                self.endpoint,
                data=self._form(page),
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Referer": f"{self.config.host.rstrip('/')}/",
                    "Origin": self.config.host.rstrip("/"),
                },
            )
            try:
                payload = response.json()
            except ValueError:
                self.log.warning("non-JSON response from %s (page %s)", self.endpoint, page)
                break

            items = payload.get("lista") or []
            if total is None:
                total = int(payload.get("quantidade") or 0)
                self.log.info("source reports %s listings for the price window", total)
            if not items:
                break

            for item in items:
                raw = self._parse_item(item)
                if raw is not None:
                    yield raw

            if total is not None and page * PAGE_SIZE >= total:
                break

    def _parse_item(self, item: dict[str, Any]) -> RawListing | None:
        city = clean_text(item.get("cidade") or "")
        if not same_city(city, self.imoview_config.city):
            self.log.debug("skipping other city: %s", city)
            return None

        code = item.get("codigoauxiliar") or item.get("codigo")
        slug = clean_text(item.get("url_amigavel") or "").strip("/")
        if not code or not slug:
            return None

        price = parse_decimal(item.get("valor"))
        area = parse_area(item.get("areaprincipal")) or parse_area(item.get("areainterna"))
        area_land = parse_area(item.get("arealote"))
        condo_fee = parse_decimal(item.get("valorcondominio"))
        kind = clean_text(item.get("tipo") or "")
        neighborhood = clean_text(item.get("bairro") or "")

        photos = [
            str(photo.get("urlp"))
            for photo in (item.get("fotos") or [])
            if isinstance(photo, dict) and photo.get("urlp")
        ]
        if not photos and item.get("urlfotoprincipalp"):
            photos = [str(item["urlfotoprincipalp"])]

        return RawListing(
            external_id=str(code),
            url=f"{self.config.host.rstrip('/')}/imovel/{slug}/{code}",
            title=clean_text(f"{kind} - {neighborhood}".strip(" -")) or clean_text(item.get("titulo") or ""),
            description=clean_text(item.get("titulo") or "") or None,
            property_type=guess_property_type(kind, item.get("titulo")),
            city=city or self.imoview_config.city,
            neighborhood=neighborhood or None,
            address=clean_text(item.get("endereco") or "") or None,
            price=Decimal(price) if price and price > 0 else None,
            area_usable=area,
            area_land=area_land,
            condo_fee=condo_fee if condo_fee and condo_fee > 0 else None,
            bedrooms=parse_int(item.get("numeroquartos")),
            bathrooms=parse_int(item.get("numerobanhos")),
            parking=parse_int(item.get("numerovagas")),
            photos=photos,
        )


__all__ = ["ImoviewConfig", "ImoviewScraper"]
