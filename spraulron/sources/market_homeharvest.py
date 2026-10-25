"""Market listings via HomeHarvest (unofficial Realtor.com client). Photos are hot-linked, never stored."""
from __future__ import annotations

import re
from typing import Any, Optional

from ..models import Listing
from .links import build_links

KEY = "homeharvest"
LABEL = "Realtor.com via HomeHarvest"
PROPERTY_TYPES = ["multi_family", "duplex_triplex"]

_UNIT_PATTERNS = [
    (re.compile(r"\b(fourplex|four-?plex|quadplex|quadruplex|4[- ]?plex|four[- ]family|4[- ]family|4[- ]units?)\b", re.I), 4),
    (re.compile(r"\b(triplex|tri-?plex|3[- ]?plex|three[- ]family|3[- ]family|3[- ]units?)\b", re.I), 3),
    (re.compile(r"\b(duplex|du-?plex|2[- ]?plex|two[- ]family|2[- ]family|2[- ]units?|double)\b", re.I), 2),
]
_N_UNITS = re.compile(r"\b(\d{1,2})\s*-?\s*(?:unit|units|family)\b", re.I)


def parse_units_from_text(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    m = _N_UNITS.search(text)
    if m and 2 <= int(m.group(1)) <= 4:
        return int(m.group(1))
    for pat, n in _UNIT_PATTERNS:
        if pat.search(text):
            return n
    return None


def _units_from_source(p: dict) -> Optional[int]:
    u = p.get("units")
    if isinstance(u, list) and u:
        return len(u)
    if isinstance(u, (int, float)) and 1 <= u <= 50:
        return int(u)
    if isinstance(u, dict):
        for k in ("count", "units", "total"):
            if isinstance(u.get(k), int):
                return u[k]
    return None


def _num(v) -> Optional[float]:
    try:
        return float(v) if v is not None and v == v else None
    except (TypeError, ValueError):
        return None


def to_listing(p: dict, county_hint: Optional[str] = None) -> Optional[Listing]:
    addr = p.get("address") or {}
    desc = p.get("description") or {}
    street = (addr.get("full_line") or addr.get("street") or "").strip()
    if not street:
        return None
    county = (p.get("county") or county_hint or "").replace(" County", "").strip() or None
    text = desc.get("text")
    units = _units_from_source(p)
    units_source = "source" if units else "unknown"
    if not units:
        t = parse_units_from_text(text) or parse_units_from_text(desc.get("type")) or parse_units_from_text(desc.get("style"))
        if t:
            units, units_source = t, "text"
    baths = (_num(desc.get("baths_full")) or 0) + 0.5 * (_num(desc.get("baths_half")) or 0)
    photos = [x for x in (desc.get("alt_photos") or []) if isinstance(x, str)]
    photo = desc.get("primary_photo") or (photos[0] if photos else None)
    tax = _num(p.get("tax"))
    advertisers = p.get("advertisers") or {}
    agent = (advertisers.get("agent") or {}).get("name") if isinstance(advertisers, dict) else None
    office = (advertisers.get("office") or {}).get("name") if isinstance(advertisers, dict) else None
    lid = str(p.get("property_id") or p.get("listing_id") or p.get("mls_id") or p.get("property_url"))
    url = p.get("property_url")
    listing = Listing(
        id=f"hh-{lid}", source=KEY, source_label=LABEL, url=url, address=street, city=addr.get("city"),
        state=addr.get("state") or "OH", zip=str(addr.get("zip") or "")[:5] or None, county=county,
        lat=_num(p.get("latitude")), lon=_num(p.get("longitude")), price=_num(p.get("list_price")),
        units=units, units_source=units_source, beds=int(desc["beds"]) if _num(desc.get("beds")) else None,
        baths=baths or None, sqft=int(desc["sqft"]) if _num(desc.get("sqft")) else None,
        lot_sqft=int(desc["lot_sqft"]) if _num(desc.get("lot_sqft")) else None,
        year_built=int(desc["year_built"]) if _num(desc.get("year_built")) else None,
        days_on_market=int(p["days_on_mls"]) if _num(p.get("days_on_mls")) else None,
        listed_date=str(p.get("list_date"))[:10] if p.get("list_date") else None, status=p.get("status") or p.get("mls_status"),
        property_type=desc.get("type") or desc.get("style"), description=text, photo=photo, photos=photos[:24],
        mls=p.get("mls"), mls_id=str(p.get("mls_id")) if p.get("mls_id") else None,
        tax_annual=tax if tax and 100 < tax < 50000 else None, assessed_value=_num(p.get("assessed_value")),
        hoa_monthly=_num(p.get("hoa_fee")), agent=agent, office=office,
    )
    listing.links = build_links(street, listing.city, listing.zip, county, None, listing.lat, listing.lon, listing_url=url)
    return listing


def fetch(client=None, settings=None, counties: Optional[list[str]] = None, limit: int = 2000) -> list[dict]:
    from homeharvest import scrape_property  # imported lazily: heavy

    counties = counties or (settings.counties.names if settings else ["Cuyahoga", "Summit", "Stark"])
    seen: set[str] = set()
    out: list[dict] = []
    errors: list[str] = []
    for county in counties:
        try:
            props = scrape_property(location=f"{county} County, OH", listing_type="for_sale",
                                    property_type=PROPERTY_TYPES, return_type="pydantic", limit=limit)
        except Exception as exc:  # one county failing should not kill the others
            errors.append(f"{county}: {type(exc).__name__}: {exc}")
            continue
        for prop in props:
            d = prop.model_dump(mode="json") if hasattr(prop, "model_dump") else dict(prop)
            listing = to_listing(d, county)
            if not listing or listing.id in seen:
                continue
            if listing.county and listing.county not in counties:
                continue
            seen.add(listing.id)
            out.append(listing.model_dump(mode="json"))
    if not out and errors:
        raise RuntimeError("; ".join(errors))
    return out
