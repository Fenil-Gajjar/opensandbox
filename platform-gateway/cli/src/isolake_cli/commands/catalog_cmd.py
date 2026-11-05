# Isolake platform-specific: catalog browsing commands.

from __future__ import annotations

import click

from isolake_cli.client import ClientContext
from isolake_cli.utils import handle_errors, output_option, prepare_output


@click.group("catalog", invoke_without_command=True)
@click.pass_context
def catalog_group(ctx: click.Context) -> None:
    """📋 Browse prebuilt sandbox catalog."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@catalog_group.command("list")
@click.option("--category", default=None, help="Filter by category.")
@click.option("--tag", default=None, help="Filter by tag.")
@output_option("table", "json", "yaml")
@click.pass_obj
@handle_errors
def catalog_list(obj: ClientContext, category: str | None, tag: str | None, output_format: str | None) -> None:
    """List available prebuilt sandbox flavors."""
    prepare_output(obj, output_format, allowed=("table", "json", "yaml"), fallback="table")
    import httpx
    config = obj.connection_config
    api_key = config.get_api_key()
    headers = {"OPEN-SANDBOX-API-KEY": api_key} if api_key else {}
    params = {}
    if category:
        params["category"] = category
    if tag:
        params["tag"] = tag
    domain = config.get_domain()
    if domain.startswith("http://") or domain.startswith("https://"):
        base = domain
    else:
        base = f"{config.protocol}://{domain}"
    with httpx.Client(base_url=base, headers=headers, timeout=30) as client:
        resp = client.get("/api/catalog", params=params)
        resp.raise_for_status()
        entries = resp.json()
    if not entries:
        obj.output.info("No catalog entries found.")
        return
    if obj.output.fmt in ("json", "yaml"):
        obj.output.print_rows(entries, columns=["slug", "name", "description", "category"])
    else:
        rows = [
            {
                "slug": e["slug"],
                "name": e["name"],
                "category": e.get("category", ""),
                "tags": ", ".join(e.get("tags", [])),
            }
            for e in entries
        ]
        obj.output.print_rows(rows, columns=["slug", "name", "category", "tags"], title="Catalog")


@catalog_group.command("info")
@click.argument("slug")
@output_option("table", "json", "yaml")
@click.pass_obj
@handle_errors
def catalog_info(obj: ClientContext, slug: str, output_format: str | None) -> None:
    """Show details of a prebuilt sandbox flavor."""
    prepare_output(obj, output_format, allowed=("table", "json", "yaml"), fallback="table")
    import httpx
    config = obj.connection_config
    api_key = config.get_api_key()
    headers = {"OPEN-SANDBOX-API-KEY": api_key} if api_key else {}
    domain = config.get_domain()
    if domain.startswith("http://") or domain.startswith("https://"):
        base = domain
    else:
        base = f"{config.protocol}://{domain}"
    with httpx.Client(base_url=base, headers=headers, timeout=30) as client:
        resp = client.get(f"/api/catalog/{slug}")
        if resp.status_code == 404:
            raise click.ClickException(f"Catalog entry '{slug}' not found.")
        resp.raise_for_status()
        entry = resp.json()
    obj.output.print_dict(entry, title=entry.get("name", slug))
