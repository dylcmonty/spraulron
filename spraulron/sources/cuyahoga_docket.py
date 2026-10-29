"""Cuyahoga Clerk of Courts sheriff-sale search page: gives each sale date a type label."""
from __future__ import annotations

import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

KEY = "cuyahoga_docket"
LABEL = "Cuyahoga Clerk docket (sale-type labels)"
URL = "https://cpdocket.cp.cuyahogacounty.gov/SheriffSearch/"

_OPT = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})\s+(Tax Delinquent|Non-Tax)", re.I)


def parse_labels(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, str] = {}
    for opt in soup.find_all("option"):
        m = _OPT.search(opt.get_text(" ", strip=True))
        if not m:
            continue
        d = datetime.strptime(m.group(1), "%m/%d/%Y").date().isoformat()
        out[d] = "Delinquent Tax" if m.group(2).lower().startswith("tax") else "Mortgage Foreclosure"
    return out


def fetch(client: httpx.Client, settings=None) -> dict[str, str]:
    r = client.get(URL)
    r.raise_for_status()
    labels = parse_labels(r.text)
    if not labels:
        raise ValueError("no sale-date labels found on Cuyahoga docket page")
    return labels
