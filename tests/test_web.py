"""Web interface tests: auth, list, triage, undo, detail and image fallback."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from imovel.config import Settings
from imovel.db import session_scope
from imovel.detail import ListingDetail
from imovel.models import Listing, Source
from imovel.server import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'web.db'}",
        rate_limit_seconds=0.0,
        admin_user="admin",
        admin_password="secret",
    )


@pytest.fixture
def client(settings, monkeypatch):
    def fake_detail(url: str, _settings) -> ListingDetail:
        return ListingDetail(
            title="Detalhe",
            description="Descrição completa do imóvel.",
            photos=["https://cdn.chavesnamao.com.br/a.jpg", "https://cdn.chavesnamao.com.br/b.jpg"],
            condo_fee=Decimal("450"),
            iptu=Decimal("120"),
            area_land=Decimal("250"),
        )

    monkeypatch.setattr("imovel.web.fetch_detail", fake_detail)

    app = create_app(settings)
    with TestClient(app) as test_client:
        with session_scope(settings) as session:
            session.add(Source(id="agency", name="Agência", type="json_ld", config={}, enabled=True))
            session.add(Source(id="portal", name="Portal", type="json_ld", config={}, enabled=True))
            session.add(
                Listing(
                    source_id="agency",
                    external_id="1",
                    url="https://example.com/imovel/1",
                    title="Casa no Centro",
                    property_type="house",
                    neighborhood="Centro",
                    price=Decimal("450000"),
                    area_usable=Decimal("150"),
                    area_land=Decimal("250"),
                    bedrooms=3,
                    bathrooms=2,
                    parking=1,
                    photos=["https://cdn.chavesnamao.com.br/1.jpg"],
                    photo_large="https://cdn.chavesnamao.com.br/1-large.jpg",
                    group_key="grupo-1",
                    is_active=True,
                )
            )
            session.add(
                Listing(
                    source_id="portal",
                    external_id="2",
                    url="https://example.com/imovel/2",
                    title="Apartamento no Claret",
                    property_type="apartment",
                    neighborhood="Jardim Claret",
                    price=Decimal("320000"),
                    area_usable=Decimal("80"),
                    bedrooms=2,
                    photos=["https://cdn.chavesnamao.com.br/2.jpg"],
                    photo_large="https://cdn.chavesnamao.com.br/2-large.jpg",
                    group_key="grupo-1",
                    is_active=True,
                )
            )
        yield test_client


def _login(client: TestClient) -> None:
    response = client.post(
        "/login", data={"username": "admin", "password": "secret"}, follow_redirects=False
    )
    assert response.status_code == 303


def test_requires_login(client):
    response = client.get("/novos", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/login"


def test_login_rejects_wrong_password(client):
    response = client.post("/login", data={"username": "admin", "password": "nope"})
    assert response.status_code == 401


def test_login_accepts_admin_credentials(client):
    _login(client)
    assert client.get("/novos").status_code == 200


def test_list_shows_cards_with_price_and_neighborhood(client):
    _login(client)
    body = client.get("/novos").text
    assert 'id="card-1"' in body and 'id="card-2"' in body
    assert "R$ 450.000" in body
    assert "2 sites" in body  # same group_key on two sources


def test_filters_apply(client):
    _login(client)
    body = client.get("/novos?preco_max=400000").text
    assert 'id="card-2"' in body
    assert 'id="card-1"' not in body


def test_triage_yes_moves_listing_to_interessados(client):
    _login(client)
    listing_id = 1

    response = client.post(
        f"/triagem/{listing_id}", data={"decision": "yes", "mode": "novos"}
    )
    assert response.status_code == 200
    assert "Desfazer" in response.text

    novos = client.get("/novos").text
    assert 'id="card-1"' not in novos

    interessados = client.get("/interessados").text
    assert 'id="card-1"' in interessados


def test_triage_maybe_goes_to_talvez(client):
    _login(client)
    client.post("/triagem/1", data={"decision": "maybe", "mode": "novos"})
    assert 'id="card-1"' in client.get("/talvez").text
    assert 'id="card-1"' not in client.get("/interessados").text


def test_undo_restores_listing_to_novos(client):
    _login(client)
    client.post("/triagem/1", data={"decision": "yes", "mode": "novos"})
    assert 'id="card-1"' not in client.get("/novos").text

    response = client.post("/triagem/1/undo", data={"mode": "novos"})
    assert response.status_code == 200
    assert 'id="card-1"' in client.get("/novos").text


def test_triage_rejects_unknown_decision(client):
    _login(client)
    assert client.post("/triagem/1", data={"decision": "banana"}).status_code == 400


def test_triage_unknown_listing(client):
    _login(client)
    assert client.post("/triagem/999", data={"decision": "yes"}).status_code == 404


def test_detail_page_uses_on_demand_data(client):
    _login(client)
    body = client.get("/imovel/1").text
    assert "Descrição completa do imóvel." in body
    assert "Também anunciado em" in body
    assert "250 m²" in body
    assert "450" in body  # condo fee from the on-demand detail


def test_image_proxy_falls_back_to_placeholder(client):
    response = client.get("/img", params={"u": "http://insecure.example.com/a.jpg"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")


def test_deck_renders_one_card_per_group(client):
    _login(client)
    body = client.get("/deck").text
    assert 'id="deck"' in body
    assert body.count('class="deck-card') == 1  # the two listings share a group
    assert "deck.js" in body


def test_deck_uses_the_large_photo(client):
    _login(client)
    body = client.get("/deck").text
    assert "2-large.jpg" in body
    assert "w=1200" in body


def test_deck_decision_returns_next_card_or_empty_state(client):
    _login(client)
    response = client.post(
        "/triagem/1",
        data={"decision": "yes", "view": "deck", "mode": "deck", "q": ""},
    )
    assert response.status_code == 200
    assert "Nenhum imóvel novo" in response.text
    assert "Desfazer" in response.text


def test_deck_undo_returns_card(client):
    _login(client)
    client.post("/triagem/1", data={"decision": "yes", "view": "deck", "q": ""})
    response = client.post("/triagem/1/undo", data={"view": "deck", "q": ""})
    assert response.status_code == 200
    assert 'data-id="1"' in response.text
