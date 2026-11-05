# Isolake platform-specific: wallet, usage, keys commands.

from __future__ import annotations

import click

from isolake_cli.client import ClientContext
from isolake_cli.utils import handle_errors, output_option, prepare_output


@click.group("platform", invoke_without_command=True)
@click.pass_context
def platform_group(ctx: click.Context) -> None:
    """💳 Platform account management (wallet, keys, usage)."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@platform_group.command("wallet")
@output_option("table", "json", "yaml")
@click.pass_obj
@handle_errors
def platform_wallet(obj: ClientContext, output_format: str | None) -> None:
    """Check wallet balance."""
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
        resp = client.get("/api/wallet")
        resp.raise_for_status()
        data = resp.json()
    obj.output.print_dict(data, title="Wallet")


@platform_group.command("usage")
@output_option("table", "json", "yaml")
@click.pass_obj
@handle_errors
def platform_usage(obj: ClientContext, output_format: str | None) -> None:
    """View usage breakdown."""
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
        resp = client.get("/api/usage")
        resp.raise_for_status()
        data = resp.json()
    obj.output.print_dict(data, title="Usage")


@platform_group.command("keys")
@output_option("table", "json", "yaml")
@click.pass_obj
@handle_errors
def platform_keys(obj: ClientContext, output_format: str | None) -> None:
    """List API keys."""
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
        resp = client.get("/api/keys")
        resp.raise_for_status()
        data = resp.json()
    obj.output.print_dict(data, title="API Keys")
