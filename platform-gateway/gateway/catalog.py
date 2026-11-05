"""
Catalog system — loads prebuilt sandbox flavors from YAML files.

Loaded once at gateway startup into memory. Served via /api/catalog endpoints.
When a user creates a sandbox with {"catalog": "chrome-browser"}, the gateway
resolves the slug to a full create payload, merges user overrides, and forwards
to the upstream server.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

CATALOG_DIR = Path(__file__).resolve().parent.parent / "catalog"

_catalog: dict[str, dict[str, Any]] = {}


def load_catalog(catalog_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load all YAML catalog files from disk into memory."""
    global _catalog
    directory = catalog_dir or CATALOG_DIR

    if not directory.is_dir():
        logger.warning("Catalog directory not found: %s", directory)
        _catalog = {}
        return _catalog

    loaded = {}
    for path in sorted(directory.glob("*.yaml")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                entry = yaml.safe_load(f)

            if not isinstance(entry, dict):
                logger.warning("Skipping %s: not a valid YAML mapping", path.name)
                continue

            slug = entry.get("slug")
            if not slug:
                logger.warning("Skipping %s: missing 'slug' field", path.name)
                continue

            if slug in loaded:
                logger.warning("Duplicate catalog slug '%s' in %s (already loaded)", slug, path.name)
                continue

            required = ("name", "description", "category", "create_params")
            missing = [k for k in required if k not in entry]
            if missing:
                logger.warning("Skipping %s: missing fields: %s", path.name, ", ".join(missing))
                continue

            loaded[slug] = entry
            logger.info("Loaded catalog entry: %s (%s)", slug, entry["name"])

        except yaml.YAMLError as exc:
            logger.error("Failed to parse %s: %s", path.name, exc)
        except OSError as exc:
            logger.error("Failed to read %s: %s", path.name, exc)

    _catalog = loaded
    logger.info("Catalog loaded: %d entries", len(_catalog))
    return _catalog


def get_catalog() -> dict[str, dict[str, Any]]:
    """Return the loaded catalog."""
    return _catalog


def get_catalog_entry(slug: str) -> dict[str, Any] | None:
    """Look up a single catalog entry by slug."""
    return _catalog.get(slug)


def list_catalog(
    category: str | None = None,
    tag: str | None = None,
) -> list[dict[str, Any]]:
    """Return catalog entries, optionally filtered by category or tag."""
    entries = list(_catalog.values())

    if category:
        entries = [e for e in entries if e.get("category", "").lower() == category.lower()]

    if tag:
        tag_lower = tag.lower()
        entries = [e for e in entries if tag_lower in [t.lower() for t in e.get("tags", [])]]

    return entries


def list_catalog_summary(
    category: str | None = None,
    tag: str | None = None,
) -> list[dict[str, Any]]:
    """Return catalog entries with only summary fields (no create_params)."""
    entries = list_catalog(category=category, tag=tag)
    return [
        {
            "slug": e["slug"],
            "name": e["name"],
            "description": e["description"],
            "category": e["category"],
            "tags": e.get("tags", []),
            "ports": e.get("ports", []),
        }
        for e in entries
    ]


def resolve_catalog_body(
    slug: str,
    user_overrides: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Resolve a catalog slug + user overrides into a full create request body.

    Returns (body, error_message). On success, error_message is None.
    On failure, body is None and error_message explains what went wrong.
    """
    entry = get_catalog_entry(slug)
    if entry is None:
        available = ", ".join(sorted(_catalog.keys())) or "(none loaded)"
        return None, f"Unknown catalog entry '{slug}'. Available: {available}"

    create_params = copy.deepcopy(entry["create_params"])
    configurable = set(entry.get("user_configurable", []))

    for key, value in user_overrides.items():
        if key == "catalog":
            continue
        if key == "metadata":
            create_params.setdefault("metadata", {})
            if isinstance(value, dict):
                create_params["metadata"].update(value)
            continue
        if key not in configurable:
            return None, (
                f"Field '{key}' is not user-configurable for catalog entry '{slug}'. "
                f"Configurable fields: {', '.join(sorted(configurable)) or '(none)'}"
            )
        if key == "env" and isinstance(value, dict):
            create_params.setdefault("env", {})
            create_params["env"].update(value)
        elif key == "resourceLimits" and isinstance(value, dict):
            create_params.setdefault("resourceLimits", {})
            create_params["resourceLimits"].update(value)
        elif key == "networkPolicy" and isinstance(value, dict):
            create_params["networkPolicy"] = value
        else:
            create_params[key] = value

    create_params.setdefault("metadata", {})
    create_params["metadata"]["isolake.catalog"] = slug

    return create_params, None
