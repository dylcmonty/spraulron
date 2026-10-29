"""Market listings via the official RentCast API (needs RENTCAST_API_KEY; free tier = 50 requests/month)."""
from __future__ import annotations

from typing import Optional

import httpx

from ..models import Listing
from .links import build_links

KEY = "rentcast"
LABEL = "RentCast API"
API = "https://api.rentcast.io/v1"


def to_listing(r: dict) -> Optional[Listing]:
    street = (r.get("addressLine1") or r.get("formattedAddress") or "").strip()
    if not street:
        return None
    county = (r.get("county") or "").replace(" County", "").strip() or None
    agent = (r.get("listingAgent") or {}).get("name")
    office = (r.get("listingOffice") or {}).get("name")
    url = f"https://app.rentcast.io/app?address={httpx.QueryParams({'a': r.get('formattedAddress', '')})['a']}" if r.get("formattedAddress") else None
    listing = Listing(
        id=f"rc-{r.get('id')}", source=KEY, source_label=LABEL, url=None, address=street, city=r.get("city"),
        state=r.get("state") or "OH", zip=str(r.get("zipCode") or "")[:5] or None, county=county,
        lat=r.get("latitude"), lon=r.get("longitude"), price=r.get("price"), units=None, units_source="unknown",
        beds=r.get("bedrooms"), baths=r.get("bathrooms"), sqft=r.get("squareFootage"), lot_sqft=r.get("lotSize"),
        year_built=r.get("yearBuilt"), days_on_market=r.get("daysOnMarket"), listed_date=(r.get("listedDate") or "")[:10] or None,
        status=r.get("status"), property_type=r.get("propertyType"), mls=r.get("mlsName"), mls_id=r.get("mlsNumber"),
        hoa_monthly=(r.get("hoa") or {}).get("fee"), agent=agent, office=office,
    )
    listing.links = build_links(street, listing.city, listing.zip, county, None, listing.lat, listing.lon)
    return listing


def fetch(client: httpx.Client, settings) -> list[dict]:
    key = settings.rentcast_api_key
    if not key:
        raise RuntimeError("RENTCAST_API_KEY not set (.env)")
    lat, lon = settings.map.center
    out: list[dict] = []
    offset = 0
    while True:
        r = client.get(f"{API}/listings/sale", headers={"X-Api-Key": key, "Accept": "application/json"},
                       params={"latitude": lat, "longitude": lon, "radius": settings.map.rentcast_radius_miles,
                               "propertyType": "Multi-Family", "status": "Active", "limit": 500, "offset": offset})
        r.raise_for_status()
        batch = r.json()
        if not isinstance(batch, list):
            raise RuntimeError(f"unexpected RentCast response: {str(batch)[:200]}")
        for rec in batch:
            listing = to_listing(rec)
            if listing and (listing.county in settings.counties.names):
                out.append(listing.model_dump(mode="json"))
        if len(batch) < 500:
            break
        offset += 500
    return out


def rent_estimate(client: httpx.Client, settings, address: str, bedrooms: Optional[int] = None,
                  bathrooms: Optional[float] = None, sqft: Optional[int] = None) -> dict:
    """On-demand per-unit rent AVM (one API request). Called only from an explicit UI action."""
    key = settings.rentcast_api_key
    if not key:
        raise RuntimeError("RENTCAST_API_KEY not set (.env)")
    params = {"address": address, "propertyType": "Multi-Family"}
    if bedrooms is not None:
        params["bedrooms"] = bedrooms
    if bathrooms is not None:
        params["bathrooms"] = bathrooms
    if sqft:
        params["squareFootage"] = sqft
    r = client.get(f"{API}/avm/rent/long-term", headers={"X-Api-Key": key, "Accept": "application/json"}, params=params)
    r.raise_for_status()
    return r.json()
