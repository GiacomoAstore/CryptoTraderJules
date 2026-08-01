"""
Crypto.com Exchange REST API v1 Authentication Helper.

Provides uniform parameter serialization and HMAC-SHA256 signature generation
shared across order_executor, api_gateway, and background scripts.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any


def params_to_str(params: dict[str, Any] | None) -> str:
    """Alphabetically sort params by key and concatenate key+value pairs into a string."""
    if not params:
        return ""
    parts: list[str] = []
    for key in sorted(params.keys()):
        parts.append(f"{key}{params[key]}")
    return "".join(parts)


def generate_cryptocom_signature(
    method: str,
    req_id: int | str,
    api_key: str,
    api_secret: str,
    params: dict[str, Any] | None,
    nonce: int | str,
) -> str:
    """Generate HMAC-SHA256 signature required by Crypto.com Exchange REST v1 API.

    sig_payload = method + id + api_key + params_str + nonce
    signature = HMAC-SHA256(api_secret, sig_payload)
    """
    param_str = params_to_str(params)
    payload = f"{method}{req_id}{api_key}{param_str}{nonce}"
    return hmac.new(
        api_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
