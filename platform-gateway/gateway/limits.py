"""
Resource limit validation.

Enforces hard caps on sandbox resource requests BEFORE they reach OpenSandbox.
Also enforces per-user concurrency limits.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

from fastapi.responses import JSONResponse
from fastapi import status

from gateway.config import settings
from gateway.ownership import get_active_sandbox_count

logger = logging.getLogger(__name__)


# ── CPU / memory parsing ────────────────────────────────────────────────────

_CPU_RE = re.compile(r"^(\d+(?:\.\d+)?)(m)?$")
_MEM_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(Ki|Mi|Gi|Ti|k|M|G|T)?$", re.IGNORECASE)

_MEM_MULTIPLIERS: Dict[str, float] = {
    "": 1,
    "k": 1e3,
    "ki": 1024,
    "m": 1e6,
    "mi": 1024**2,
    "g": 1e9,
    "gi": 1024**3,
    "t": 1e12,
    "ti": 1024**4,
}


def parse_cpu(value: str) -> float:
    """
    Parse a Kubernetes-style CPU value into vCPU float.

    "500m" → 0.5, "2" → 2.0, "1.5" → 1.5
    """
    m = _CPU_RE.match(value.strip())
    if not m:
        return 1.0  # default if unparseable
    num = float(m.group(1))
    if m.group(2) == "m":
        num /= 1000.0
    return num


def parse_memory_gib(value: str) -> float:
    """
    Parse a Kubernetes-style memory value into GiB float.

    "512Mi" → 0.5, "2Gi" → 2.0, "1024Mi" → 1.0
    """
    m = _MEM_RE.match(value.strip())
    if not m:
        return 1.0  # default if unparseable
    num = float(m.group(1))
    suffix = (m.group(2) or "").lower()
    bytes_val = num * _MEM_MULTIPLIERS.get(suffix, 1)
    return bytes_val / (1024**3)  # convert to GiB


def extract_resources(body: Dict[str, Any]) -> Tuple[float, float]:
    """
    Extract CPU (vCPU) and memory (GiB) from a CreateSandboxRequest body.

    Returns (cpu_vcpu, memory_gib) with defaults of (1.0, 1.0).
    """
    res = body.get("resourceLimits", {})
    if isinstance(res, dict):
        cpu = parse_cpu(str(res.get("cpu", "2")))
        mem = parse_memory_gib(str(res.get("memory", "2Gi")))
    else:
        cpu, mem = 2.0, 2.0
    return cpu, mem


def inject_resource_defaults(body: Dict[str, Any]) -> None:
    """
    Inject default resource limits if user didn't specify them.

    CRITICAL: Without this, OpenSandbox would create a pod with NO resource
    limits, letting it consume the entire node. We enforce:
      - cpu: default 1 if missing
      - memory: default 1Gi if missing
      - ephemeral-storage: default 10Gi if missing (cap at 100Gi)

    Mutates `body` in-place.
    """
    if "resourceLimits" not in body or not isinstance(body.get("resourceLimits"), dict):
        body["resourceLimits"] = {}

    res = body["resourceLimits"]

    # Inject CPU default
    if "cpu" not in res:
        res["cpu"] = "2"

    # Inject memory default
    if "memory" not in res:
        res["memory"] = "2Gi"

    # Inject ephemeral-storage default (sandbox disk space)
    if "ephemeral-storage" not in res:
        res["ephemeral-storage"] = "10Gi"


def validate_resource_limits(body: Dict[str, Any]) -> Optional[JSONResponse]:
    """
    Validate that the requested resources don't exceed hard caps.

    Also validates ephemeral-storage limits.
    Returns None if OK, or a 400 JSONResponse with details.
    """
    # Inject defaults first — ensures every sandbox has resource limits
    inject_resource_defaults(body)

    cpu, mem = extract_resources(body)

    if cpu > settings.max_cpu_per_sandbox:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "code": "RESOURCE_LIMIT_EXCEEDED",
                "message": f"Max {settings.max_cpu_per_sandbox} vCPU per sandbox. Requested: {cpu}",
            },
        )

    if mem > settings.max_memory_gib_per_sandbox:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "code": "RESOURCE_LIMIT_EXCEEDED",
                "message": f"Max {settings.max_memory_gib_per_sandbox} GiB RAM per sandbox. Requested: {mem:.1f} GiB",
            },
        )

    # Validate ephemeral-storage
    res = body.get("resourceLimits", {})
    storage_str = res.get("ephemeral-storage", "10Gi")
    storage_gib = parse_memory_gib(str(storage_str))
    if storage_gib > settings.max_storage_gib_per_sandbox:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "code": "RESOURCE_LIMIT_EXCEEDED",
                "message": (
                    f"Max {settings.max_storage_gib_per_sandbox} GiB storage per sandbox. "
                    f"Requested: {storage_gib:.1f} GiB"
                ),
            },
        )

    # Validate timeout if present
    timeout = body.get("timeout")
    if timeout is not None:
        try:
            t = int(timeout)
            if t > settings.max_timeout_seconds:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={
                        "code": "RESOURCE_LIMIT_EXCEEDED",
                        "message": (
                            f"Max timeout is {settings.max_timeout_seconds}s "
                            f"({settings.max_timeout_seconds // 86400} days). Requested: {t}s"
                        ),
                    },
                )
        except (ValueError, TypeError):
            pass  # let upstream validate

    return None


async def check_concurrency(user_id: str) -> Optional[JSONResponse]:
    """
    Verify user hasn't exceeded max concurrent sandboxes.

    Returns None if OK, or a 429 JSONResponse.
    """
    count = await get_active_sandbox_count(user_id)
    if count >= settings.max_concurrent_sandboxes:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "code": "CONCURRENCY_LIMIT",
                "message": (
                    f"Max {settings.max_concurrent_sandboxes} concurrent sandboxes. "
                    f"You have {count}. Delete existing sandboxes first."
                ),
            },
        )
    return None
