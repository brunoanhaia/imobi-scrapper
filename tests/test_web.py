"""Web interface tests: auth, list, triage, undo, detail and image fallback."""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from imovel.config import Settings
from imovel.db import session_scope
from imovel.detail import ListingDetail
from imovel.models import Listing, Source
from imovel.models import ListingDetail as ListingDetailRow
from imovel.server import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'web.db'}",
        rate_limit_seconds=0.0,
        admin_user="admin",
        admin_password="secret",  # nosec B106 - test fixture, not a real secret
        backup_dir=str(tmp_path / "backups"),
        state_dir=str(tmp_path),
    )


@pytest.fixture(autouse=True)
def _no_external_validation(monkeypatch):
    """The deck validates the top link on render; tests must never hit the network."""
    monkeypatch.setattr("imovel.web.validate_listing", lambda *args, **kwargs: True)


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
        "/login", data={"username": "admin", "password": "secret"}, follow_redirects=False  # nosec B105
    )
    assert response.status_code == 303


def test_requires_login(client):
    response = client.get("/novos", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/login"


def test_login_rejects_wrong_password(client):
    response = client.post(
        "/login",
        data={"username": "admin", "password": "wrong-password"},  # nosec B105 - test only
    )
    assert response.status_code == 401


def test_login_accepts_admin_credentials(client):
    _login(client)
    assert client.get("/novos").status_code == 200


def test_list_shows_cards_with_price_and_neighborhood(client):
    _login(client)
    body = client.get("/novos").text
    assert 'id="card-1"' in body
    assert 'id="card-2"' in body
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


def test_detail_renders_instantly_without_touching_the_portal(client, monkeypatch):
    _login(client)

    def boom(*_args, **_kwargs):
        raise AssertionError("the main page must not wait for the external fetch")

    monkeypatch.setattr("imovel.web.fetch_detail", boom)
    response = client.get("/imovel/1")

    assert response.status_code == 200
    assert 'id="detail-extra"' in response.text
    assert "450.000" in response.text


def test_detail_extra_enriches_the_page(client):
    _login(client)
    body = client.get("/imovel/1/extra").text
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
    assert "deck-enhance.js" in body
    assert 'name="decision"' in body
    assert 'name="next"' in body


def test_deck_uses_the_large_photo(client):
    _login(client)
    body = client.get("/deck").text
    assert "2-large.jpg" in body
    assert "w=1200" in body


def test_deck_form_post_redirects_with_flash(client):
    _login(client)
    response = client.post(
        "/triagem/1",
        data={"decision": "yes", "next": "/deck"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/deck?")
    assert "flash=yes" in response.headers["location"]

    page = client.get(response.headers["location"]).text
    assert "Interessa salvo" in page


def test_deck_rejects_absolute_redirect(client):
    _login(client)
    response = client.post(
        "/triagem/1",
        data={"decision": "yes", "next": "https://evil.example.com/"},
        follow_redirects=False,
    )

    assert response.status_code == 200  # no redirect for untrusted targets


def test_deck_failure_renders_the_same_card_again(client):
    _login(client)
    response = client.post(
        "/triagem/999",
        data={"decision": "yes", "next": "/deck"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert "flash=error" in location
    assert "card=999" in location


def test_deck_failed_card_is_rendered_with_the_flag(client):
    _login(client)
    page = client.get("/deck?flash=error&card=1").text

    assert "Não deu para salvar" in page
    assert "card-failed" in page


def test_card_does_not_send_the_user_straight_to_the_portal(client):
    _login(client)
    body = client.get("/novos").text
    assert "example.com/imovel/1" not in body


def test_gone_listing_is_deactivated_and_flagged(client, monkeypatch):
    _login(client)
    from imovel.detail import DetailFetchError

    def gone(url: str, _settings):
        raise DetailFetchError(f"HTTP 410 for {url}", status=410)

    monkeypatch.setattr("imovel.web.fetch_detail", gone)

    body = client.get("/imovel/2/extra").text
    assert "saiu do ar" in body
    assert "Ver no site" not in body

    with session_scope(client.app.state.settings) as session:
        listing = session.scalar(select(Listing).where(Listing.external_id == "2"))
        assert listing.is_active is False


def test_cached_gone_error_deactivates_without_refetch(client, monkeypatch):
    """A listing_detail row with a cached 410 must not keep the listing alive."""
    _login(client)

    with session_scope(client.app.state.settings) as session:
        session.add(ListingDetailRow(listing_id=2, photos=[], error="HTTP 410 for x"))

    def boom(*_args, **_kwargs):
        raise AssertionError("should not fetch again inside the retry window")

    monkeypatch.setattr("imovel.web.fetch_detail", boom)
    body = client.get("/imovel/2/extra").text

    assert "saiu do ar" in body


def test_deck_skips_a_listing_whose_page_is_gone(client, monkeypatch):
    """A 410 must not be rendered, even if the listing is still flagged active."""
    _login(client)

    import imovel.web as web

    calls: list[int] = []

    def fake_validate(session, settings, listing, client=None, ttl=None):
        calls.append(listing.id)
        if listing.id == 2:  # the most recent unseen listing
            listing.is_active = False
            return False
        return True

    monkeypatch.setattr(web, "validate_listing", fake_validate)

    body = client.get("/deck").text
    assert 'data-id="2"' not in body
    assert calls  # validation ran


def test_deck_still_renders_when_the_check_fails(client, monkeypatch):
    """Transport errors must never hide a property."""
    _login(client)

    import imovel.web as web

    monkeypatch.setattr(web, "validate_listing", lambda *a, **k: True)

    body = client.get("/deck").text
    assert 'class="deck-card' in body


def test_undo_with_next_returns_to_the_deck(client):
    _login(client)
    client.post("/triagem/1", data={"decision": "yes", "next": "/deck"})

    response = client.post("/triagem/1/undo", data={"next": "/deck"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/deck"
    # the listing is unseen again, so it is back in the list
    assert 'id="card-1"' in client.get("/novos").text


def test_deck_flash_offers_undo(client):
    _login(client)
    page = client.get("/deck?flash=maybe&decided=1").text

    assert "Talvez salvo" in page
    assert "/triagem/1/undo" in page


def _csv_header(text: str) -> list[str]:
    return text.lstrip("\ufeff").splitlines()[0].split(";")


def test_export_csv_contains_every_field_and_the_url(client):
    _login(client)
    response = client.get("/export.csv")

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    header = _csv_header(response.text)
    assert header[0] == "id"
    assert "url" in header
    assert "photos" in header
    assert "interested" in header
    assert "price_previous" in header
    assert len(header) == len(set(header))
    assert len(header) >= 30


def test_export_csv_has_rows_and_a_real_url(client):
    _login(client)
    lines = client.get("/export.csv").text.lstrip("\ufeff").splitlines()

    assert len(lines) >= 3  # header + two listings
    assert "https://example.com/imovel/1" in "\n".join(lines)


def test_export_csv_respects_filters_and_scope(client):
    _login(client)
    client.post("/triagem/1", data={"decision": "yes", "next": "/deck"})

    novos = client.get("/export.csv?scope=novos").text
    assert "example.com/imovel/1" not in novos
    assert "example.com/imovel/2" in novos

    interessados = client.get("/export.csv?scope=yes").text
    assert "example.com/imovel/1" in interessados
    assert "example.com/imovel/2" not in interessados


def test_export_json_has_number_types_and_all_keys(client):
    _login(client)
    payload = client.get("/export.json").json()

    assert isinstance(payload, list)
    assert len(payload) >= 2
    first = payload[0]
    assert first["url"].startswith("https://")
    assert isinstance(first["price"], float)
    assert "interested" in first
    assert "seen" in first
    assert "photos" in first


def test_export_requires_login(client):
    assert client.get("/export.csv", follow_redirects=False).status_code == 307


def test_admin_export_includes_triage(client):
    """The back-office CSV must carry the decision too, not only listing columns."""
    _login(client)
    response = client.get("/admin/listing/export/csv")

    assert response.status_code == 200
    body = response.text.lstrip("\ufeff")
    header = body.splitlines()[0].split(";")
    assert "url" in header
    assert "interested" in header
    assert "seen" in header
    assert "notes" in header


# --------------------------------------------------------------------------- backup


def _csrf(client: TestClient) -> str:
    response = client.get("/backup")
    assert response.status_code == 200
    match = re.search(r'name="csrf" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def test_backup_page_requires_login(client):
    response = client.get("/backup", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/login"


def test_backup_download_returns_a_sqlite_dump(client):
    _login(client)
    token = _csrf(client)
    response = client.post("/backup/download", data={"csrf": token})
    assert response.status_code == 200
    assert response.content.startswith(b"SQLite format 3\x00")
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"


def test_backup_download_rejects_a_missing_csrf(client):
    _login(client)
    response = client.post("/backup/download", data={})
    assert response.status_code == 422


def test_restore_roundtrip_restores_the_previous_state(client, settings):
    _login(client)
    dump = client.post("/backup/download", data={"csrf": _csrf(client)}).content

    with session_scope(settings) as session:
        session.add(
            Listing(
                source_id="agency",
                external_id="after-dump",
                url="https://example.com/imovel/after",
                title="Anúncio criado depois do dump",
                is_active=True,
            )
        )
    with session_scope(settings) as session:
        assert session.scalar(select(Listing).where(Listing.external_id == "after-dump")) is not None

    token = _csrf(client)
    response = client.post(
        "/backup/restore",
        data={"csrf": token, "password": "secret", "confirmation": "RESTAURAR"},  # nosec B105
        files={"file": ("backup.dump", dump, "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with session_scope(settings) as session:
        assert session.scalar(select(Listing).where(Listing.external_id == "after-dump")) is None
    safety = client.app.state.backup.safety_dumps()
    assert len(safety) == 1
    assert safety[0]["name"].startswith("pre-restore-")
    download = client.get(f"/backup/safety/{safety[0]['name']}")
    assert download.status_code == 200
    assert download.content.startswith(b"SQLite format 3\x00")


def test_restore_rejects_a_wrong_password(client):
    _login(client)
    token = _csrf(client)
    response = client.post(
        "/backup/restore",
        data={"csrf": token, "password": "nope", "confirmation": "RESTAURAR"},  # nosec B105
        files={"file": ("backup.dump", b"not a dump", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert "Senha do admin" in response.text


def test_restore_rejects_an_invalid_archive(client):
    _login(client)
    token = _csrf(client)
    response = client.post(
        "/backup/restore",
        data={"csrf": token, "password": "secret", "confirmation": "RESTAURAR"},  # nosec B105
        files={"file": ("backup.dump", b"not a dump", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert "inválido" in response.text


def test_restore_requires_the_confirmation_word(client):
    _login(client)
    token = _csrf(client)
    response = client.post(
        "/backup/restore",
        data={"csrf": token, "password": "secret", "confirmation": "sim"},  # nosec B105
        files={"file": ("backup.dump", b"whatever", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert "RESTAURAR" in response.text


def test_maintenance_blocks_the_triage_pages(client):
    _login(client)
    client.app.state.backup.maintenance.set()
    try:
        assert client.get("/novos").status_code == 503
        assert client.get("/backup").status_code == 200
    finally:
        client.app.state.backup.maintenance.clear()


def test_backup_can_be_disabled(settings, monkeypatch):
    monkeypatch.setattr("imovel.web.fetch_detail", lambda *args, **kwargs: None)
    disabled = settings.model_copy(update={"backup_enabled": False})
    app = create_app(disabled)
    with TestClient(app) as client:
        _login(client)
        assert client.get("/backup").status_code == 404


def test_allowed_hosts_rejects_unknown_domains(settings):
    restricted = settings.model_copy(update={"allowed_hosts": "imoveis.example.com"})
    app = create_app(restricted)
    with TestClient(app) as client:
        assert client.get("/login", headers={"host": "imoveis.example.com"}).status_code == 200
        assert client.get("/login", headers={"host": "evil.example"}).status_code == 400
