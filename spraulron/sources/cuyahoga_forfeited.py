"""Cuyahoga County Forfeited Land Sale parcels (public ArcGIS FeatureServer behind the county dashboard)."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Optional

import httpx

from ..models import AuctionItem
from .links import build_links, money

KEY = "cuyahoga_forfeited"
LABEL = "Cuyahoga County Forfeited Land Sale"
LAYER = "https://services7.arcgis.com/GXM8JipKyc0m6HBi/arcgis/rest/services/2024_Forfeited_Land_Sale_Update/FeatureServer/0"
OFFICIAL = "https://cuyahogacounty.gov/fiscal-officer/departments/real-property/forfeited-lands"
FIELDS = ["parcel_id", "par_addr_all", "parcel_addr", "parcel_predir", "parcel_street", "parcel_suffix", "parcel_city",
          "parcel_zip", "parcel_owner", "prop_class_desc", "res_bldg_count", "total_res_liv_area", "tax_market_total",
          "Sale_Date", "PPN", "CASE_", "DEFENDANT", "OPENING_BID", "legal_description", "parcel_acreage", "res_styles"]


def _epoch(v) -> Optional[date]:
    if v in (None, ""):
        return None
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000, tz=timezone.utc).date()
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def to_item(feature: dict) -> AuctionItem:
    a = feature.get("attributes", {})
    geom = feature.get("geometry") or {}
    street = " ".join(str(a.get(k) or "").strip() for k in ("parcel_addr", "parcel_predir", "parcel_street", "parcel_suffix")).strip()
    street = re.sub(r"\s+", " ", street) or (a.get("par_addr_all") or "").split(",")[0]
    city = (a.get("parcel_city") or "").title() or None
    zip_code = str(a.get("parcel_zip") or "")[:5] or None
    parcel = a.get("parcel_id") or a.get("PPN")
    pclass = a.get("prop_class_desc")
    bldgs = a.get("res_bldg_count") or 0
    vacant = ("VACANT" in str(pclass).upper()) or (bldgs == 0 and "LAND" in str(pclass).upper())
    sale_date = _epoch(a.get("Sale_Date"))
    opening = money(a.get("OPENING_BID"))
    liv = a.get("total_res_liv_area")
    notes = " | ".join(x for x in (
        f"{pclass}" if pclass else None,
        f"{bldgs} bldg(s), {int(liv):,} sqft living" if liv else None,
        f"Defendant: {a.get('DEFENDANT')}" if a.get("DEFENDANT") else None,
        f"Styles: {a.get('res_styles')}" if a.get("res_styles") else None,
    ) if x) or None
    item = AuctionItem(
        id=f"cuyforf-{parcel}", source=KEY, source_label=LABEL, county="Cuyahoga", sale_type="Forfeited Land",
        sale_date=sale_date, status="Listed on county forfeited-land map (verify availability)",
        address=street, city=city, zip=zip_code, parcel=parcel, case_number=a.get("CASE_"),
        appraised=money(a.get("tax_market_total")), appraised_label="County market value", opening_bid=opening,
        is_vacant_lot=vacant, property_class=pclass, url=OFFICIAL, notes=notes,
        lat=geom.get("y"), lon=geom.get("x"),
    )
    item.links = build_links(street, city, zip_code, "Cuyahoga", parcel, item.lat, item.lon, listing_url=OFFICIAL)
    return item


def fetch(client: httpx.Client, settings=None, page_size: int = 1000) -> list[dict]:
    items: list[dict] = []
    offset = 0
    while True:
        r = client.get(f"{LAYER}/query", params={"where": "1=1", "outFields": ",".join(FIELDS), "f": "json",
                                                "outSR": 4326, "returnGeometry": "true", "resultOffset": offset,
                                                "resultRecordCount": page_size})
        r.raise_for_status()
        j = r.json()
        if "error" in j:
            raise RuntimeError(f"ArcGIS error: {j['error']}")
        feats = j.get("features", [])
        items.extend(to_item(f).model_dump(mode="json") for f in feats)
        if len(feats) < page_size or not j.get("exceededTransferLimit", False):
            break
        offset += page_size
    return items
