"""Command line interface (typer)."""

from __future__ import annotations

import json
import logging
import sys
from decimal import Decimal

import typer
from sqlalchemy import select

from .config import get_settings
from .db import session_scope
from .models import Listing, Run, Source, Triage
from .pipeline import run_all
from .probe import probe as run_probe
from .seed import seed as run_seed
from .sources import describe_types, load_all

app = typer.Typer(help="Aggregate for-sale real estate listings in Rio Claro/SP.", no_args_is_help=True)


def _setup_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        stream=sys.stderr,
    )


@app.command()
def seed() -> None:
    """Create tables, default criteria and starter sources (idempotent)."""
    _setup_logging()
    stats = run_seed(get_settings())
    typer.echo(json.dumps(stats, indent=2))


@app.command()
def scrape(
    source: list[str] | None = typer.Option(None, "--source", "-s", help="Only these source ids."),
    min_price: int | None = typer.Option(None, "--min-price", help="Override criteria min price."),
    max_price: int | None = typer.Option(None, "--max-price", help="Override criteria max price."),
) -> None:
    """Collect every enabled source (or the selected ones)."""
    _setup_logging()
    settings = get_settings()
    only = set(source) if source else None
    runs = run_all(settings, only=only, min_price=min_price, max_price=max_price)
    if not runs:
        typer.secho("no source ran (check `imovel sources`)", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    failures = 0
    typer.echo("")
    typer.echo(f"{'SOURCE':<20} {'STATUS':<8} {'FETCH':>6} {'KEPT':>6} {'NEW':>5} {'OUT':>5} {'DUP/OOS':>8}")
    for run in runs:
        typer.echo(
            f"{run.source_id:<20} {run.status:<8} {run.fetched:>6} {run.kept:>6} "
            f"{run.created:>5} {run.skipped_out_of_range:>5} "
            f"{run.skipped_invalid + run.skipped_out_of_scope:>8}"
        )
        if run.status == "failed":
            failures += 1
            typer.secho(f"  error: {run.error}", fg=typer.colors.RED)

    if failures == len(runs):
        raise typer.Exit(code=1)


@app.command()
def sources() -> None:
    """List configured sources and their last run."""
    _setup_logging()
    settings = get_settings()
    with session_scope(settings) as session:
        rows = session.scalars(select(Source).order_by(Source.id)).all()
        if not rows:
            typer.echo("no sources configured; run `imovel seed` first")
            return
        for source in rows:
            last = session.scalar(
                select(Run)
                .where(Run.source_id == source.id)
                .order_by(Run.started_at.desc())
                .limit(1)
            )
            state = "enabled" if source.enabled else "disabled"
            last_txt = (
                f"{last.started_at:%Y-%m-%d %H:%M} {last.status} kept={last.kept}"
                if last
                else "never run"
            )
            typer.echo(f"{source.id:<20} {source.type:<14} {state:<9} {last_txt}")


@app.command()
def types() -> None:
    """List registered scraper types and their capabilities."""
    load_all()
    typer.echo(json.dumps(describe_types(), indent=2, ensure_ascii=False))


@app.command()
def probe(
    url: str = typer.Argument(..., help="Listing page URL of the site to inspect."),
    city: str = typer.Option("Rio Claro", "--city"),
) -> None:
    """Fingerprint a site and suggest the scraper type + config."""
    _setup_logging()
    result = run_probe(url, get_settings(), city=city)
    typer.echo(f"url ............ {result.url}")
    typer.echo(f"status ......... {result.status}")
    typer.echo(f"size ........... {result.size} bytes")
    typer.echo(f"title .......... {result.title}")
    typer.echo(f"type sugerido .. {result.suggested_type} (confidence: {result.confidence})")
    typer.echo(f"evidencias ..... {', '.join(result.evidence) or '-'}")
    typer.echo("config sugerida:")
    typer.echo(
        json.dumps({"type": result.suggested_type, **result.suggested_config}, indent=2, ensure_ascii=False)
    )


@app.command()
def stats() -> None:
    """Quick counters for the database."""
    _setup_logging()
    settings = get_settings()
    with session_scope(settings) as session:
        listings = session.query(Listing).count()
        active = session.query(Listing).filter(Listing.is_active.is_(True)).count()
        triaged = session.query(Triage).filter(Triage.seen.is_(True)).count()
        prices = [row for row in session.scalars(select(Listing.price)).all() if row is not None]
    typer.echo(
        f"listings={listings} active={active} seen={triaged} "
        f"min={min(prices) if prices else '-'} max={max(prices) if prices else '-'}"
    )


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Run the web UI (/admin) with uvicorn."""
    _setup_logging()
    import uvicorn

    from .server import create_app

    uvicorn.run(create_app(), host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()


_ = Decimal
