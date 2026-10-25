"""Cuyahoga Land Bank available properties (static HTML cards with hot-linkable photos)."""
from __future__ import annotations

import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from ..models import AuctionItem
from .links import build_links

KEY = "cuyahoga_landbank"
LABEL = "Cuyahoga Land Bank"
URL = "https://cuyahogalandbank.org/all-available-properties/"
BASE = "https://cuyahogalandbank.org"


def parse_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for card in soup.find_all("div", class_="parcel-card"):
        rec: dict = {}
        img = card.find("img")
        if img:
            rec["photo"] = img.get("data-src") or (img.get("src") if not str(img.get("src", "")).startswith("data:") else None)
        link = card.find("a", href=re.compile(r"parcel_id="))
        if link:
            rec["url"] = BASE + link["href"] if link["href"].startswith("/") else link["href"]
            m = re.search(r"parcel_id=([\w-]+)", link["href"])
            rec["parcel"] = m.group(1) if m else None
        overlay = card.find(class_="parcel-card-overlay")
        rec["overlay"] = overlay.get_text(" ", strip=True) if overlay else None
        ps = [p for p in card.find_all("p") if not p.find("span")]
        texts = [p.get_text("\n", strip=True) for p in ps]
        addr_block = next((t for t in texts if re.search(r"\d", t) and "OH" in t.upper()), None)
        if addr_block:
            lines = [l.strip() for l in addr_block.split("\n") if l.strip()]
            rec["address"] = lines[0]
            rec["city_line"] = lines[1] if len(lines) > 1 else ""
        for p in card.find_all("p"):
            lab = p.find(class_="parcel-card-label")
            val = p.find(class_="parcel-card-value")
            if lab and val:
                rec[lab.get_text(strip=True).rstrip(":").lower()] = val.get_text(" ", strip=True)
        if rec.get("address"):
            out.append(rec)
    return out


def to_item(rec: dict) -> AuctionItem:
    m = re.match(r"(.*?),\s*OH\s*(\d{5})?", rec.get("city_line", ""), re.I)
    city = m.group(1).strip() if m else None
    zip_code = m.group(2) if m and m.group(2) else None
    status = rec.get("status") or rec.get("overlay")
    item = AuctionItem(
        id=f"cuylb-{rec.get('parcel')}", source=KEY, source_label=LABEL, county="Cuyahoga", sale_type="Land Bank",
        status=status, address=rec["address"], city=city, zip=zip_code, parcel=rec.get("parcel"),
        photo=rec.get("photo"), url=rec.get("url"), is_vacant_lot=("lot" in (status or "").lower()),
        notes=f"Neighborhood: {rec['neighborhood']}" if rec.get("neighborhood") else None,
    )
    item.links = build_links(item.address, city, zip_code, "Cuyahoga", item.parcel, listing_url=item.url)
    return item


def fetch(client: httpx.Client, settings=None) -> list[dict]:
    r = client.get(URL)
    r.raise_for_status()
    cards = parse_cards(r.text)
    if not cards:
        raise ValueError("no parcel cards found on Cuyahoga Land Bank page")
    return [to_item(c).model_dump(mode="json") for c in cards]
