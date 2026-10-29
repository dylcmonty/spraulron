"""Rent benchmarks (HUD Fair Market Rents) and per-unit rent estimation.

HUD USER API: https://www.huduser.gov/hudapi/public/fmr/data/{entityid}?year=YYYY
County entity id = state FIPS + county FIPS + "99999" (e.g. Cuyahoga 3903599999).
Without a token we fall back to data/fmr_fallback.json (rough estimates, clearly labelled).
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Optional

import httpx
from pydantic import BaseModel, Field

from ..config import Settings
from ..models import Unit

HUD_BASE = "https://www.huduser.gov/hudapi/public/fmr"
BEDROOM_KEYS = ["Efficiency", "One-Bedroom", "Two-Bedroom", "Three-Bedroom", "Four-Bedroom"]


class RentTable(BaseModel):
    source: str = "estimate"
    year: Optional[str] = None
    county_fmr: dict[str, list[float]] = Field(default_factory=dict)  # county -> [0br..4br]
    zip_fmr: dict[str, list[float]] = Field(default_factory=dict)     # zip -> [0br..4br]
    zip_county: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    def rel_sd(self, bedrooms: Optional[int]) -> Optional[float]:
        """Relative standard deviation of ZIP-level FMRs for this bedroom count (None if too few ZIPs)."""
        b = min(max(bedrooms if bedrooms is not None else 2, 0), 4)
        vals = [v[b] for v in self.zip_fmr.values() if v and v[b]]
        if len(vals) < 10:
            return None
        mean = statistics.fmean(vals)
        return statistics.pstdev(vals) / mean if mean else None

    def fmr(self, zip_code: Optional[str], county: Optional[str], bedrooms: Optional[int]) -> tuple[Optional[float], str]:
        b = min(max(bedrooms if bedrooms is not None else 2, 0), 4)
        if zip_code and zip_code[:5] in self.zip_fmr:
            return self.zip_fmr[zip_code[:5]][b], f"SAFMR {zip_code[:5]}"
        if county and county in self.county_fmr:
            return self.county_fmr[county][b], f"FMR {county}"
        if self.county_fmr:
            vals = [v[b] for v in self.county_fmr.values()]
            return statistics.median(vals), "FMR region median"
        return None, "none"


def _row_to_list(row: dict) -> Optional[list[float]]:
    try:
        return [float(row[k]) for k in BEDROOM_KEYS]
    except (KeyError, TypeError, ValueError):
        return None


def parse_hud_payload(payload: dict, county: str) -> tuple[Optional[list[float]], dict[str, list[float]], Optional[str]]:
    data = payload.get("data", payload)
    basic = data.get("basicdata")
    year = str(data.get("year") or (basic.get("year") if isinstance(basic, dict) else None) or "")
    zips: dict[str, list[float]] = {}
    county_row: Optional[list[float]] = None
    if isinstance(basic, dict):
        county_row = _row_to_list(basic)
    elif isinstance(basic, list):
        for row in basic:
            vals = _row_to_list(row)
            if vals is None:
                continue
            z = str(row.get("zip_code") or "").strip()
            if len(z) == 5 and z.isdigit():
                zips[z] = vals
            else:
                county_row = county_row or vals
        if county_row is None and zips:
            county_row = [statistics.median(v[i] for v in zips.values()) for i in range(5)]
    return county_row, zips, year or None


def fetch_hud_table(client: httpx.Client, settings: Settings, year: Optional[int] = None) -> dict:
    token = settings.hud_api_token
    if not token:
        raise RuntimeError("HUD_API_TOKEN not set")
    table = RentTable(source="HUD FMR API", notes=[])
    for county, fips in settings.counties.fips.items():
        params = {"year": year} if year else {}
        r = client.get(f"{HUD_BASE}/data/{fips}99999", params=params, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        county_row, zips, yr = parse_hud_payload(r.json(), county)
        if county_row:
            table.county_fmr[county] = county_row
        for z, vals in zips.items():
            table.zip_fmr[z] = vals
            table.zip_county[z] = county
        table.year = table.year or yr
    if not table.county_fmr:
        raise ValueError("HUD API returned no FMR rows")
    return table.model_dump()


def load_fallback_table(settings: Settings) -> RentTable:
    p = settings.root / "data" / "fmr_fallback.json"
    with open(p, encoding="utf-8") as fh:
        return RentTable(**json.load(fh))


def distribute_bedrooms(units: int, total_beds: Optional[int]) -> list[Optional[int]]:
    """Spread the building's total bedroom count across units (larger units first)."""
    if units <= 0:
        return []
    if total_beds is None or total_beds <= 0:
        return [None] * units
    base, extra = divmod(int(total_beds), units)
    beds = [base + (1 if i < extra else 0) for i in range(units)]
    return [min(max(b, 0), 4) for b in beds]


def estimate_units(units: int, total_beds: Optional[int], zip_code: Optional[str], county: Optional[str],
                   table: RentTable, factor: float = 1.0, overrides: Optional[dict] = None,
                   rel_sd_fallback: float = 0.15) -> list[Unit]:
    """Build Unit records with rent estimates; apply per-listing overrides (unit_beds, rents)."""
    overrides = overrides or {}
    beds_list = overrides.get("unit_beds") or distribute_bedrooms(units, total_beds)
    beds_list = list(beds_list)[:units] + [None] * max(0, units - len(beds_list))
    rents_override = overrides.get("rents") or []
    out: list[Unit] = []
    for i, b in enumerate(beds_list):
        rent_ov = rents_override[i] if i < len(rents_override) else None
        if rent_ov:
            out.append(Unit(bedrooms=b, rent=float(rent_ov), rent_source="override"))
            continue
        fmr, basis = table.fmr(zip_code, county, b if b is not None else 2)
        rent = round(fmr * factor) if fmr is not None else None
        src = "fmr" if table.source.startswith("HUD") else "estimate"
        rel = table.rel_sd(b) or rel_sd_fallback
        lo, hi = (round(rent * (1 - rel)), round(rent * (1 + rel))) if rent is not None else (None, None)
        out.append(Unit(bedrooms=b, rent=rent, rent_source=f"{src}:{basis}", rent_low=lo, rent_high=hi))
    # owner takes the lowest-rent unit
    if out:
        idx = min(range(len(out)), key=lambda i: (out[i].rent if out[i].rent is not None else 1e9))
        out[idx].owner_occupied = True
    return out


def rent_totals(units: list[Unit]) -> tuple[Optional[float], Optional[float]]:
    rents = [u.rent for u in units if u.rent is not None]
    if not rents or len(rents) != len(units):
        return None, None
    tenant = sum(u.rent for u in units if not u.owner_occupied)
    return float(tenant), float(sum(rents))
