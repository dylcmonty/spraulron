"""Shared HTTP client factory."""
from __future__ import annotations

import httpx

from .config import Settings


def make_client(settings: Settings, timeout: float = 40.0, **kwargs) -> httpx.Client:
    headers = {
        "User-Agent": settings.app.user_agent,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    }
    headers.update(kwargs.pop("headers", {}))
    return httpx.Client(headers=headers, timeout=timeout, follow_redirects=True, **kwargs)
