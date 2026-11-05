# Copyright 2026 Alibaba Group Holding Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Root Click group with global options."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from isolake_cli import __version__
from isolake_cli.client import ClientContext
from isolake_cli.commands.command import command_group
from isolake_cli.commands.config_cmd import config_group
from isolake_cli.commands.credential_vault import credential_vault_group
from isolake_cli.commands.devops import devops_group
from isolake_cli.commands.diagnostics import diagnostics_group
from isolake_cli.commands.egress import egress_group
from isolake_cli.commands.file import file_group
from isolake_cli.commands.sandbox import sandbox_group
from isolake_cli.commands.skills import skills_group
from isolake_cli.commands.catalog_cmd import catalog_group
from isolake_cli.commands.platform_cmd import platform_group
from isolake_cli.config import resolve_config

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

BANNER = r"""[bold cyan]
  ___           _       _
 |_ _|___  ___ | | __ _| | _____
  | |/ __|/ _ \| |/ _` | |/ / _ \
  | |\__ \ (_) | | (_| |   <  __/
 |___|___/\___/|_|\__,_|_|\_\___|
[/]  [dim]v{version}[/]
"""


class BannerGroup(click.Group):
    """Custom Click group that shows a banner before help text."""

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        console = Console(stderr=False)
        console.print(BANNER.format(version=__version__))
        super().format_help(ctx, formatter)


@click.group(cls=BannerGroup, context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--api-key", envvar="ISOLAKE_API_KEY", default=None, help="API key for authentication.")
@click.option("--domain", envvar="ISOLAKE_DOMAIN", default=None, help="API server domain (e.g. localhost:8080).")
@click.option("--protocol", type=click.Choice(["http", "https"]), default=None, help="Protocol (http/https).")
@click.option("--request-timeout", type=int, default=None, help="Request timeout in seconds.")
@click.option("--config", "config_path", type=click.Path(exists=False, path_type=Path), default=None, help="Config file path.")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose/debug output.")
@click.option("--no-color", is_flag=True, default=False, help="Disable colored output.")
@click.version_option(version=__version__, prog_name="isolake")
@click.pass_context
def cli(
    ctx: click.Context,
    api_key: str | None,
    domain: str | None,
    protocol: str | None,
    request_timeout: int | None,
    config_path: Path | None,
    verbose: bool,
    no_color: bool,
) -> None:
    """Isolake CLI — manage sandboxes from your terminal."""
    if verbose:
        import logging

        logging.basicConfig(level=logging.DEBUG)

    cli_overrides = {
        "api_key": api_key,
        "domain": domain,
        "protocol": protocol,
        "request_timeout": request_timeout,
    }

    if ctx.invoked_subcommand == "config":
        resolved = {
            "api_key": api_key,
            "domain": domain,
            "protocol": protocol or "http",
            "request_timeout": request_timeout or 30,
            "color": True,
            "default_image": None,
            "default_timeout": None,
        }
    else:
        resolved = resolve_config(
            cli_api_key=api_key,
            cli_domain=domain,
            cli_protocol=protocol,
            cli_timeout=request_timeout,
            config_path=config_path,
        )
    resolved["color"] = not no_color and resolved.get("color", True)

    effective_config_path = config_path or Path.home() / ".isolake" / "config.toml"
    ctx.obj = ClientContext(
        resolved_config=resolved,
        config_path=effective_config_path,
        cli_overrides=cli_overrides,
    )
    ctx.call_on_close(lambda: ctx.obj.close())


# Register sub-command groups
cli.add_command(sandbox_group)
cli.add_command(command_group)
cli.add_command(file_group)
cli.add_command(egress_group)
cli.add_command(credential_vault_group)
cli.add_command(config_group)
cli.add_command(diagnostics_group)
cli.add_command(devops_group)
cli.add_command(skills_group)
cli.add_command(catalog_group)
cli.add_command(platform_group)
