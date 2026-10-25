"""Market listings via HomeHarvest (unofficial Realtor.com client). Photos are hot-linked, never stored."""
from __future__ import annotations

import re
from typing import Any, Optional

from ..models import Listing
from .links import build_links

KEY = "homeharvest"
LABEL = "Realtor.com via HomeHarvest"
PROPERTY_TYPES = ["multi_family", "duplex_triplex"]

_KIND = r"(?:family|fam|units?|suites?|apartments?|apts?)"
_UNIT_PATTERNS = [
    (re.compile(rf"\b(fourplex|four-?plex|quadplex|quadruplex|4[- ]?plex|four[- ]?{_KIND}|4[- ]?{_KIND})\b", re.I), 4),
    (re.compile(rf"\b(triplex|tri-?plex|3[- ]?plex|three[- ]?{_KIND}|3[- ]?{_KIND})\b", re.I), 3),
    (re.compile(rf"\b(duplex|du-?plex|2[- ]?plex|two[- ]?{_KIND}|2[- ]?{_KIND}|both (?:units|sides|suites|apartments|apts|floors)"
                rf"|side[- ]by[- ]side|up(?:stairs)?[ /&-]+(?:and[ -])?down(?:stairs)?)\b", re.I), 2),
    # NE Ohio "double" = two-family; skip double lot/garage/sink etc.
    (re.compile(r"\bdouble\b(?![ -](?:lot|garage|car|sink|vanity|hung|pane|oven|door|wide|closet|driveway|glazed|bowl|parlor))", re.I), 2),
]
_N_UNITS = re.compile(rf"\b(\d{{1,2}})\s*-?\s*{_KIND}\b", re.I)
_UNIT_ENUM = re.compile(r"\b(?:unit|suite|apartment|apt)\s*#?\s*([1-4])\b", re.I)
_FLOOR_UNIT = re.compile(r"\b(first|1st|second|2nd|third|3rd)[- ]floor (?:unit|apartment|suite|apt)\b", re.I)


def parse_units_from_text(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    m = _N_UNITS.search(text)
    if m and 2 <= int(m.group(1)) <= 4:
        return int(m.group(1))
    for pat, n in _UNIT_PATTERNS:
        if pat.search(text):
            return n
    enum = [int(n) for n in _UNIT_ENUM.findall(text)]  # "Unit 1 ... Unit 3"
    if enum and max(enum) >= 2:
        return max(enum)
    floors = [f.lower() for f in _FLOOR_UNIT.findall(text)]  # "2nd floor unit"
    if floors:
        return 3 if any(f in ("third", "3rd") for f in floors) else 2
    return None


def assume_units(beds: Optional[float], baths: Optional[float]) -> tuple[int, str]:
    """Guess a 2–4 unit count when the listing never states one.

    Small multifamily almost always has one bath per unit, so bathrooms are the best proxy
    (about 80% right on listings that do state a count); beds ÷ 2 is the fallback.
    """
    if baths:
        n, basis = int(round(baths)), f"{baths:g} baths"
    elif beds:
        n, basis = int(round(beds / 2)), f"{int(beds)} beds"
    else:
        n, basis = 2, "default"
    return min(max(n, 2), 4), basis


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


def _stats(xs: list[float]) -> dict:
    n = len(xs)
    if n < 3:
        return {"n": n}
    import math
    import statistics
    out = {"n": n, "mean": statistics.fmean(xs), "sd": statistics.pstdev(xs), "median": statistics.median(xs)}
    logs = [math.log(x) for x in xs if x > 0]
    if len(logs) >= 3:  # prices are right-skewed: a log-space band is the honest ±1 SD
        out["log_mean"], out["log_sd"] = statistics.fmean(logs), statistics.pstdev(logs)
    return out


def sold_stats_from_records(records: list[dict]) -> dict:
    """Aggregate recent sales into sale÷list ratios and sold $/unit by unit count. Nothing per-property is kept."""
    ratios: list[float] = []
    per_unit: dict[int, list[float]] = {}
    for rec in records:
        lp, sp = _num(rec.get("list_price")), _num(rec.get("sold_price"))
        if lp and sp and 0.4 <= sp / lp <= 1.6:
            ratios.append(sp / lp)
        if sp and sp >= 10000:
            u = parse_units_from_text(rec.get("text")) or assume_units(_num(rec.get("beds")), _num(rec.get("full_baths")))[0]
            per_unit.setdefault(u, []).append(sp / u)
    return {"ratio": _stats(ratios), "per_unit": {str(u): _stats(v) for u, v in sorted(per_unit.items())},
            "all_per_unit": _stats([x for v in per_unit.values() for x in v])}


def fetch_sold_stats(client=None, settings=None, counties: Optional[list[str]] = None, past_days: int = 180) -> dict:
    from homeharvest import scrape_property  # imported lazily: heavy

    counties = counties or (settings.counties.names if settings else ["Cuyahoga", "Summit", "Stark"])
    out: dict = {"past_days": past_days, "counties": {}, "errors": []}
    for county in counties:
        try:
            df = scrape_property(location=f"{county} County, OH", listing_type="sold", past_days=past_days, property_type=PROPERTY_TYPES)
            out["counties"][county] = sold_stats_from_records(df.to_dict("records"))
        except Exception as exc:
            out["errors"].append(f"{county}: {type(exc).__name__}: {exc}")
    if not out["counties"]:
        raise RuntimeError("; ".join(out["errors"]) or "no sold data")
    return out


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
