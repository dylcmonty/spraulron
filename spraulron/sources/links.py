"""Outbound links so images/details are viewed at the source instead of stored locally."""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote, quote_plus

COUNTY_PARCEL_SEARCH = {
    "Cuyahoga": "https://myplace.cuyahogacounty.gov/",
    "Summit": "https://fiscaloffice.summitoh.net/index.php/property-tax-search",
    "Stark": "https://ddti.starkcountyohio.gov/",
}
COUNTY_SHERIFF_SALES = {
    "Cuyahoga": "https://cuyahoga.sheriffsaleauction.ohio.gov/",
    "Summit": "https://summit.sheriffsaleauction.ohio.gov/",
    "Stark": "https://sheriffsales.starkcountyohio.gov/",
}


def full_address(address: str, city: Optional[str], state: str = "OH", zip_code: Optional[str] = None) -> str:
    parts = [address.strip()]
    if city:
        parts.append(city.strip().title() if city.isupper() else city.strip())
    tail = state
    if zip_code:
        tail += f" {zip_code[:5]}"
    parts.append(tail)
    return ", ".join(p for p in parts if p)


def zillow_search(addr: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", addr).strip("-")
    return f"https://www.zillow.com/homes/{slug}_rb/"


def redfin_search(addr: str) -> str:
    return f"https://www.redfin.com/search#query={quote_plus(addr)}"


def realtor_search(addr: str) -> str:
    return f"https://www.realtor.com/search?query={quote_plus(addr)}"


def google_maps(addr: str) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(addr)}"


def street_view(lat: Optional[float], lon: Optional[float], addr: Optional[str] = None) -> Optional[str]:
    if lat is not None and lon is not None:
        return f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat:.6f},{lon:.6f}"
    if addr:
        return f"https://www.google.com/maps?q={quote_plus(addr)}&layer=c"
    return None


def map_embed(addr: str) -> str:
    """Keyless Google Maps embed (iframe src)."""
    return f"https://maps.google.com/maps?q={quote_plus(addr)}&z=16&output=embed"


def county_parcel(county: Optional[str], parcel: Optional[str] = None) -> Optional[str]:
    base = COUNTY_PARCEL_SEARCH.get(county or "")
    if not base:
        return None
    if county == "Cuyahoga" and parcel:
        digits = re.sub(r"\D", "", parcel)
        if len(digits) == 8:
            return f"{base}?parcelid={digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return base


def build_links(address: str, city: Optional[str], zip_code: Optional[str], county: Optional[str],
                parcel: Optional[str] = None, lat: Optional[float] = None, lon: Optional[float] = None,
                listing_url: Optional[str] = None) -> dict[str, str]:
    addr = full_address(address, city, "OH", zip_code)
    links: dict[str, str] = {}
    if listing_url:
        links["source"] = listing_url
    links["zillow"] = zillow_search(addr)
    links["realtor"] = realtor_search(addr)
    links["google_maps"] = google_maps(addr)
    sv = street_view(lat, lon, addr)
    if sv:
        links["street_view"] = sv
    links["map_embed"] = map_embed(addr)
    cp = county_parcel(county, parcel)
    if cp:
        links["county_parcel"] = cp
    return links


def is_vacant_lot_text(text: Optional[str]) -> bool:
    if not text:
        return False
    t = text.upper()
    return "VACANT" in t or bool(re.search(r"\bLOTS?\b", t) and not re.search(r"\d{2,}\s+\w", t.split("LOT")[0] or ""))


def money(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    s = re.sub(r"[^0-9.\-]", "", str(text))
    if not s or s in {".", "-"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None
