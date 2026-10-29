"""Address geocoding with a tiny persistent cache (data/geocache.json).

Census batch geocoder first (free, no key), Nominatim fallback (rate-limited) for a handful of misses.
"""
from __future__ import annotations

import csv
import io
import json
import re
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

import httpx

CENSUS_BATCH = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
NOMINATIM = "https://nominatim.openstreetmap.org/search"


def norm_key(street: str, city: Optional[str], zip_code: Optional[str]) -> str:
    return re.sub(r"\s+", " ", f"{street}|{city or ''}|{(zip_code or '')[:5]}".upper()).strip()


class Geocoder:
    def __init__(self, path: Path, user_agent: str, nominatim_budget: int = 15):
        self.path = Path(path)
        self.user_agent = user_agent
        self.nominatim_budget = nominatim_budget
        self._lock = threading.Lock()
        self.cache: dict[str, dict] = {}
        if self.path.exists():
            try:
                self.cache = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.cache = {}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cache, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.path)

    def lookup(self, street: str, city: Optional[str], zip_code: Optional[str]) -> Optional[tuple[float, float]]:
        e = self.cache.get(norm_key(street, city, zip_code))
        if e and e.get("lat") is not None:
            return e["lat"], e["lon"]
        return None

    def geocode_many(self, rows: Iterable[tuple[str, Optional[str], Optional[str]]], client: Optional[httpx.Client] = None) -> dict[str, tuple[float, float]]:
        """rows: (street, city, zip). Returns {norm_key: (lat, lon)} for everything resolvable."""
        rows = [r for r in rows if r[0]]
        missing = {}
        for street, city, z in rows:
            k = norm_key(street, city, z)
            if k not in self.cache and not re.search(r"VACANT|^LOTS?\b", street.upper()):
                missing[k] = (street, city, z)
        own = client is None
        client = client or httpx.Client(timeout=90, headers={"User-Agent": self.user_agent})
        try:
            if missing:
                self._census_batch(client, missing)
            still = [k for k in missing if k not in self.cache]
            for k in still[: self.nominatim_budget]:
                self._nominatim(client, k, *missing[k])
            for k in still:
                self.cache.setdefault(k, {"lat": None, "lon": None, "src": "unresolved", "ts": int(time.time())})
            with self._lock:
                self._save()
        finally:
            if own:
                client.close()
        out = {}
        for street, city, z in rows:
            k = norm_key(street, city, z)
            e = self.cache.get(k)
            if e and e.get("lat") is not None:
                out[k] = (e["lat"], e["lon"])
        return out

    def _census_batch(self, client: httpx.Client, missing: dict[str, tuple]) -> None:
        keys = list(missing)
        for start in range(0, len(keys), 5000):
            chunk = keys[start:start + 5000]
            buf = io.StringIO()
            w = csv.writer(buf)
            for i, k in enumerate(chunk):
                street, city, z = missing[k]
                w.writerow([i, street, city or "", "OH", (z or "")[:5]])
            try:
                r = client.post(CENSUS_BATCH, data={"benchmark": "Public_AR_Current"},
                                files={"addressFile": ("addresses.csv", buf.getvalue().encode(), "text/csv")}, timeout=120)
                r.raise_for_status()
            except Exception:
                return
            for row in csv.reader(io.StringIO(r.text)):
                if len(row) < 6 or row[2] != "Match":
                    continue
                try:
                    lon, lat = (float(x) for x in row[5].split(","))
                except ValueError:
                    continue
                k = chunk[int(row[0])]
                self.cache[k] = {"lat": lat, "lon": lon, "src": "census", "ts": int(time.time())}

    def _nominatim(self, client: httpx.Client, key: str, street: str, city: Optional[str], z: Optional[str]) -> None:
        q = ", ".join(p for p in (street, city, "OH", (z or "")[:5]) if p)
        try:
            r = client.get(NOMINATIM, params={"q": q, "format": "json", "limit": 1, "countrycodes": "us"},
                           headers={"User-Agent": self.user_agent}, timeout=30)
            time.sleep(1.1)
            j = r.json()
            if j:
                self.cache[key] = {"lat": float(j[0]["lat"]), "lon": float(j[0]["lon"]), "src": "nominatim", "ts": int(time.time())}
        except Exception:
            return
