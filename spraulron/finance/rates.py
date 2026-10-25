"""Live OHFA (Ohio Housing Finance Agency) mortgage rates from https://ohiohome.org/rates/."""
from __future__ import annotations

import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

OHFA_URL = "https://ohiohome.org/rates/"

_PCT = re.compile(r"(\d{1,2}\.\d{1,3})\s*%")


def _pct(cell_text: str) -> Optional[float]:
    m = _PCT.search(cell_text)
    return float(m.group(1)) / 100 if m else None


def parse_ohfa(html: str) -> dict:
    """Return rates for the Traditional First-Time Homebuyer Program (first column of the first table)."""
    soup = BeautifulSoup(html, "lxml")
    out: dict = {"source": OHFA_URL, "program": "OHFA First-Time Homebuyer Program"}
    m = re.search(r"Updated\s+(\d{1,2}/\d{1,2}/\d{2,4}(?:\s+at\s+[\d:]+\s*[ap]m)?)", soup.get_text(" ", strip=True), re.I)
    if m:
        out["updated"] = m.group(1)
    tables = soup.find_all("table")
    target = None
    for t in tables:
        txt = t.get_text(" ", strip=True)
        if "First-time Homebuyer Program" in txt and "FHA" in txt:
            target = t
            break
    if target is None and tables:
        target = tables[0]
    if target is None:
        return out
    section = None
    for tr in target.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if not cells:
            continue
        row_txt = " ".join(cells)
        if re.search(r"FHA.*VA.*USDA", row_txt, re.I):
            section = "fha"
            continue
        if "Conventional" in row_txt and len(cells) == 1:
            section = "conv"
            continue
        label = cells[0].lower()
        values = [_pct(c) for c in cells[1:]]
        values = [v for v in values if v is not None]
        if not values or section is None:
            continue
        if label.startswith("with no ohfa"):
            out[f"{section}_no_dpa"] = values[0]
        elif "dpa" in label:
            out[f"{section}_dpa"] = values[0]
            mm = re.search(r"(\d(?:\.\d)?)\s*%", cells[0])
            if mm:
                out[f"{section}_dpa_pct"] = float(mm.group(1)) / 100
    return out


def fetch_ohfa_rates(client: httpx.Client) -> dict:
    r = client.get(OHFA_URL)
    r.raise_for_status()
    data = parse_ohfa(r.text)
    if "fha_no_dpa" not in data:
        raise ValueError("OHFA rate table layout changed; FHA rate not found")
    return data
