"""Orchestration: run sources through the cache, enrich rows with rents + FHA math, manage overrides."""
from __future__ import annotations

import math

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

from .cache import CacheResult, JsonCache
from .config import Settings
from .finance import fha as fha_mod
from .finance.rates import fetch_ohfa_rates
from .finance.rents import RentTable, estimate_units, fetch_hud_table, load_fallback_table, rent_totals
from .geocode import Geocoder
from .http import make_client
from .models import FhaAssumptions, Listing
from .sources import (cuyahoga_docket, cuyahoga_forfeited, cuyahoga_landbank, market_homeharvest, market_rentcast,
                      realauction, stark_sheriff)
from .sources.market_homeharvest import parse_units_from_text, assume_units

RENOVATION_RE = re.compile(r"unfinished|renovat|rehab|as[- ]is|handyman|investor special|203\s*k|fixer|needs work|gut", re.I)
MIXED_USE_RE = re.compile(r"storefront|commercial|retail|mixed[- ]use|office space", re.I)


def _mean(xs: list) -> Optional[float]:
    vals = [x for x in xs if x is not None]
    return sum(vals) / len(vals) if vals else None


def price_range(price: Optional[float], units: Optional[int], sold: dict, past_days: Optional[int] = None, min_n: int = 8) -> Optional[dict]:
    """±1 SD bands for what a listing might actually sell for, from recent county sales (aggregates only).

    'sale'  : list price × (sale÷list ratio mean ± SD) — what this asking price tends to close at.
    'comps' : units × (sold $/unit mean ± SD) for the same unit count (falls back to all multifamily).
    """
    out: dict = {"past_days": past_days}
    r = sold.get("ratio") or {}
    if price and r.get("n", 0) >= min_n:
        out["sale"] = {"low": price * (r["mean"] - r["sd"]), "mid": price * r["median"], "high": price * (r["mean"] + r["sd"]),
                       "n": r["n"], "mean": r["mean"], "sd": r["sd"]}
    pu, basis = (sold.get("per_unit") or {}).get(str(units)) or {}, f"{units}-unit"
    if pu.get("n", 0) < min_n:
        pu, basis = sold.get("all_per_unit") or {}, "all multifamily"
    if units and pu.get("n", 0) >= min_n:
        if "log_mean" in pu:  # geometric band: exp(mean ± sd of log $/unit)
            lo, hi = math.exp(pu["log_mean"] - pu["log_sd"]), math.exp(pu["log_mean"] + pu["log_sd"])
        else:
            lo, hi = max(pu["mean"] - pu["sd"], 0), pu["mean"] + pu["sd"]
        out["comps"] = {"low": units * lo, "mid": units * pu["median"], "high": units * hi, "n": pu["n"], "basis": basis}
    return out if ("sale" in out or "comps" in out) else None


class Services:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache = JsonCache(settings.cache_path)
        self.geocoder = Geocoder(settings.data_path / "geocache.json", settings.app.user_agent)
        self.overrides_path = settings.data_path / "overrides.json"
        self._ov_lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="src")

    # ------------------------------------------------------------------ sources
    def source_specs(self) -> dict[str, dict]:
        s = self.settings
        return {
            "ohfa_rates": {"label": "OHFA mortgage rates", "ttl": s.ttl_seconds("ohfa_rates", 12), "page": "both"},
            "hud_fmr": {"label": "HUD Fair Market Rents", "ttl": s.ttl_seconds("hud_fmr", 720), "page": "market"},
            "cuyahoga_docket": {"label": cuyahoga_docket.LABEL, "ttl": s.ttl_seconds("cuyahoga_docket", 6), "page": "auctions"},
            "realauction_summit": {"label": realauction.COUNTIES["summit"]["label"], "ttl": s.ttl_seconds("realauction", 3), "page": "auctions"},
            "realauction_cuyahoga": {"label": realauction.COUNTIES["cuyahoga"]["label"], "ttl": s.ttl_seconds("realauction", 3), "page": "auctions"},
            "stark_sheriff": {"label": stark_sheriff.LABEL, "ttl": s.ttl_seconds("stark_sheriff", 6), "page": "auctions"},
            "cuyahoga_forfeited": {"label": cuyahoga_forfeited.LABEL, "ttl": s.ttl_seconds("cuyahoga_forfeited", 24), "page": "auctions"},
            "cuyahoga_landbank": {"label": cuyahoga_landbank.LABEL, "ttl": s.ttl_seconds("cuyahoga_landbank", 24), "page": "auctions"},
            "homeharvest": {"label": market_homeharvest.LABEL, "ttl": s.ttl_seconds("homeharvest", 6), "page": "market"},
            "sold_stats": {"label": "Realtor.com recent sales (spreads only)", "ttl": s.ttl_seconds("sold_stats", 24), "page": "market"},
            "rentcast": {"label": market_rentcast.LABEL, "ttl": s.ttl_seconds("rentcast", 24), "page": "market"},
        }

    def _fetcher(self, key: str, **kw) -> Callable[[], Any]:
        s = self.settings

        def with_client(fn):
            def run():
                with make_client(s) as client:
                    return fn(client)
            return run

        if key == "ohfa_rates":
            return with_client(lambda c: fetch_ohfa_rates(c))
        if key == "hud_fmr":
            return with_client(lambda c: fetch_hud_table(c, s))
        if key == "cuyahoga_docket":
            return with_client(lambda c: cuyahoga_docket.fetch(c, s))
        if key == "realauction_summit":
            return with_client(lambda c: realauction.fetch(c, s, "summit", docket_labels=None))
        if key == "realauction_cuyahoga":
            labels = kw.get("docket_labels")
            return with_client(lambda c: realauction.fetch(c, s, "cuyahoga", docket_labels=labels))
        if key == "stark_sheriff":
            return with_client(lambda c: stark_sheriff.fetch(c, s, include_completed=kw.get("include_completed", False)))
        if key == "cuyahoga_forfeited":
            return with_client(lambda c: cuyahoga_forfeited.fetch(c, s))
        if key == "cuyahoga_landbank":
            return with_client(lambda c: cuyahoga_landbank.fetch(c, s))
        if key == "homeharvest":
            return lambda: market_homeharvest.fetch(None, s)
        if key == "sold_stats":
            return lambda: market_homeharvest.fetch_sold_stats(None, s, past_days=s.market.sold_past_days)
        if key == "rentcast":
            return with_client(lambda c: market_rentcast.fetch(c, s))
        raise KeyError(key)

    def run(self, key: str, force: bool = False, **kw) -> CacheResult:
        spec = self.source_specs()[key]
        cache_key = key if key != "stark_sheriff" or not kw.get("include_completed") else "stark_sheriff_completed"
        return self.cache.get_or_fetch(cache_key, spec["ttl"], self._fetcher(key, **kw), force=force)

    def status(self, key: str, res: CacheResult) -> dict:
        spec = self.source_specs()[key]
        st = res.status(spec["label"], spec["ttl"])
        st["key"] = key
        return st

    # ------------------------------------------------------------------ rates & rents
    def rates(self, force: bool = False) -> tuple[dict, dict]:
        res = self.run("ohfa_rates", force=force)
        data = res.data if isinstance(res.data, dict) and res.data.get("fha_no_dpa") else {}
        if not data:
            f = self.settings.fha
            data = {"fha_no_dpa": f.rate_fallback, "fha_dpa": f.rate_dpa_fallback, "source": "config.toml fallback", "updated": None}
        return data, self.status("ohfa_rates", res)

    def rent_table(self, force: bool = False) -> tuple[RentTable, Optional[dict]]:
        if self.settings.hud_api_token:
            res = self.run("hud_fmr", force=force)
            if isinstance(res.data, dict) and res.data.get("county_fmr"):
                return RentTable(**res.data), self.status("hud_fmr", res)
            st = self.status("hud_fmr", res)
            table = load_fallback_table(self.settings)
            table.notes.append(f"HUD fetch failed ({st.get('error')}); using estimates")
            return table, st
        return load_fallback_table(self.settings), None

    # ------------------------------------------------------------------ overrides
    def load_overrides(self) -> dict[str, dict]:
        if not self.overrides_path.exists():
            return {}
        try:
            return json.loads(self.overrides_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_override(self, listing_id: str, data: Optional[dict]) -> dict[str, dict]:
        with self._ov_lock:
            ov = self.load_overrides()
            if data is None:
                ov.pop(listing_id, None)
            else:
                cur = ov.get(listing_id, {})
                cur.update({k: v for k, v in data.items() if v is not None})
                for k in [k for k, v in cur.items() if v in ("", [], None)]:
                    cur.pop(k)
                if cur:
                    ov[listing_id] = cur
                else:
                    ov.pop(listing_id, None)
            tmp = self.overrides_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(ov, indent=1), encoding="utf-8")
            tmp.replace(self.overrides_path)
            return ov

    # ------------------------------------------------------------------ assumptions
    def assumptions_from_params(self, p: dict) -> tuple[FhaAssumptions, dict, dict]:
        rates, rate_status = self.rates()
        with_dpa = str(p.get("dpa", "0")).lower() in ("1", "true", "yes", "on")
        rate = _f(p.get("rate"))
        if rate is None:
            rate = rates.get("fha_dpa" if with_dpa else "fha_no_dpa")
        a = fha_mod.default_assumptions(self.settings, rate=rate, with_dpa=with_dpa)
        for src, dst in (("down_pct", "down_payment_pct"), ("closing_pct", "closing_cost_pct"), ("reserve_pct", "vacancy_repair_reserve_pct"),
                         ("rent_factor", "market_rent_factor"), ("target", "target_cashflow"), ("tax_rate", "tax_rate"),
                         ("insurance", "insurance_monthly"), ("utilities", "utilities_monthly"), ("reserve_months", "reserve_months")):
            v = _f(p.get(src))
            if v is not None:
                setattr(a, dst, int(v) if dst == "reserve_months" else v)
        return a, rates, rate_status

    # ------------------------------------------------------------------ market
    def market(self, assumptions: FhaAssumptions, force: bool = False, sources: Optional[list[str]] = None) -> dict:
        keys = sources or ["homeharvest"] + (["rentcast"] if self.settings.rentcast_api_key else [])
        futures = {k: self.pool.submit(self.run, k, force) for k in keys}
        sold_future = self.pool.submit(self.run, "sold_stats", force)
        results = {k: f.result() for k, f in futures.items()}
        sold_res = sold_future.result()
        sold_stats = sold_res.data if isinstance(sold_res.data, dict) else {}
        table, rent_status = self.rent_table(force=force)
        statuses = [self.status(k, r) for k, r in results.items()] + [self.status("sold_stats", sold_res)]
        if rent_status:
            statuses.append(rent_status)
        warnings = [f"{st['label']}: {st['error']}" for st in statuses if st.get("error")]
        if not table.source.startswith("HUD"):
            warnings.append("Rents are rough estimates: set HUD_API_TOKEN in .env for official Fair Market Rents.")
        rows: list[dict] = []
        seen_addr: set[str] = set()
        overrides = self.load_overrides()
        for key, res in results.items():
            for raw in res.data or []:
                listing = Listing(**raw)
                addr_key = re.sub(r"\W+", "", f"{listing.address}{listing.zip or ''}").upper()
                if addr_key in seen_addr and listing.source == "rentcast":
                    continue  # HomeHarvest row already covers this address
                seen_addr.add(addr_key)
                rows.append(self.enrich_listing(listing, assumptions, table, overrides.get(listing.id, {}), sold_stats))
        return {"rows": rows, "sources": statuses, "warnings": warnings,
                "rent_table": {"source": table.source, "year": table.year, "notes": table.notes},
                "sold_stats": {"past_days": sold_stats.get("past_days"), "counties": {c: {"ratio_n": v.get("ratio", {}).get("n", 0), "sales_n": v.get("all_per_unit", {}).get("n", 0)} for c, v in (sold_stats.get("counties") or {}).items()}}}

    def enrich_listing(self, listing: Listing, base: FhaAssumptions, table: RentTable, ov: dict, sold_stats: Optional[dict] = None) -> dict:
        s = self.settings
        row = listing.model_dump(mode="json")
        units = ov.get("units") or listing.units
        units_source = "override" if ov.get("units") else listing.units_source
        if not units:
            t = parse_units_from_text(listing.description)
            if t:
                units, units_source = t, "text"
        flags = list(listing.flags)
        if not units:  # keep the rent projection alive with a clearly flagged guess
            units, basis = assume_units(listing.beds, listing.baths)
            units_source = "assumed"
            flags.append(f"units assumed ({units}) from {basis}")
        if listing.description and RENOVATION_RE.search(listing.description):
            flags.append("renovation / as-is wording")
        if listing.description and MIXED_USE_RE.search(listing.description):
            flags.append("mixed-use wording")
        tenant_rent = all_rent = None
        unit_list: list[dict] = []
        if units:
            units_models = estimate_units(units, listing.beds, listing.zip, listing.county, table, base.market_rent_factor, ov,
                                          rel_sd_fallback=s.market.rent_rel_sd_fallback)
            tenant_rent, all_rent = rent_totals(units_models)
            unit_list = [u.model_dump() for u in units_models]
            if ov.get("rents"):
                flags.append("rents overridden")
        a = fha_mod.resolve_assumptions(base, s, listing.county, listing.city, units)
        quote = None
        if listing.price:
            limit = s.fha.loan_limits.get(str(min(max(units or 1, 1), 4)))
            quote = fha_mod.quote(listing.price, units, a, tenant_rent=tenant_rent, all_units_rent=all_rent,
                                  tax_annual=listing.tax_annual, loan_limit=limit)
            flags.extend(quote.flags)
        row.update({
            "units_final": units, "units_source": units_source, "units_assumed": units_source == "assumed",
            "unit_list": unit_list, "tenant_rent": tenant_rent,
            "rent_per_unit": (all_rent / units) if all_rent and units else None,
            "rent_per_unit_low": _mean([u.get("rent_low") for u in unit_list]),
            "rent_per_unit_high": _mean([u.get("rent_high") for u in unit_list]),
            "price_range": price_range(listing.price, units, (sold_stats or {}).get("counties", {}).get(listing.county or "", {}), (sold_stats or {}).get("past_days")),
            "all_units_rent": all_rent, "fha": quote.model_dump() if quote else None, "flags": sorted(set(flags)),
            "override": ov, "starred": bool(ov.get("starred")), "hidden": bool(ov.get("hidden")), "notes": ov.get("notes"),
            "tax_rate_used": a.tax_rate,
        })
        return row

    # ------------------------------------------------------------------ auctions
    def auctions(self, assumptions: FhaAssumptions, force: bool = False, include_completed: bool = False) -> dict:
        docket = self.run("cuyahoga_docket", force=force)
        labels = docket.data if isinstance(docket.data, dict) else {}
        futures = {
            "realauction_summit": self.pool.submit(self.run, "realauction_summit", force),
            "realauction_cuyahoga": self.pool.submit(self.run, "realauction_cuyahoga", force, docket_labels=labels),
            "stark_sheriff": self.pool.submit(self.run, "stark_sheriff", force, include_completed=include_completed),
            "cuyahoga_forfeited": self.pool.submit(self.run, "cuyahoga_forfeited", force),
            "cuyahoga_landbank": self.pool.submit(self.run, "cuyahoga_landbank", force),
        }
        results = {k: f.result() for k, f in futures.items()}
        statuses = [self.status("cuyahoga_docket", docket)] + [self.status(k, r) for k, r in results.items()]
        warnings = [f"{st['label']}: {st['error']}" for st in statuses if st.get("error")]
        rows: list[dict] = []
        for key, res in results.items():
            rows.extend(res.data or [])
        self._geocode_rows(rows)
        today = date.today().isoformat()
        for row in rows:
            self._auction_finance(row, assumptions, today)
        return {"rows": rows, "sources": statuses, "warnings": warnings, "docket_labels": labels}

    def _geocode_rows(self, rows: list[dict]) -> None:
        need = [(r["address"], r.get("city"), r.get("zip")) for r in rows if r.get("lat") is None and r.get("address")]
        if not need:
            return
        try:
            with make_client(self.settings, timeout=120) as client:
                found = self.geocoder.geocode_many(need, client)
        except Exception:
            found = {}
        from .geocode import norm_key
        for r in rows:
            if r.get("lat") is None and r.get("address"):
                hit = found.get(norm_key(r["address"], r.get("city"), r.get("zip")))
                if hit:
                    r["lat"], r["lon"] = hit
                    r["links"]["street_view"] = f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={hit[0]:.6f},{hit[1]:.6f}"

    def _auction_finance(self, row: dict, base: FhaAssumptions, today: str) -> None:
        bid = row.get("opening_bid")
        deposit = row.get("deposit")
        cash = {"deposit": deposit, "balance_due": (bid - deposit) if (bid is not None and deposit is not None) else bid,
                "balance_days": 30, "notes": []}
        if row.get("source", "").startswith("realauction"):
            cash["notes"] += ["Deposit due to register/bid; balance due within 30 days of court confirmation.",
                              "No interior inspection; title and liens are the buyer's problem; an FHA mortgage is rarely possible here."]
        elif row.get("source") == "stark_sheriff":
            cash["notes"] += ["Stark sales are in person (Arts Hall, Stark County Fairgrounds, 10:00 AM). Check the county notice for deposit rules."]
        elif row.get("source") == "cuyahoga_forfeited":
            cash["notes"] += ["Forfeited-land sales are county auditor auctions; the sale date on the map may be the last sale, verify on the county page."]
        elif row.get("source") == "cuyahoga_landbank":
            cash["notes"] += ["Land bank properties are sold by application/offer, not auction; pricing is on the detail page."]
        if row.get("county") == "Cuyahoga" and row.get("sale_type") == "Delinquent Tax":
            cash["notes"].append("Cuyahoga requires a notarized no-delinquent-taxes affidavit before title transfers.")
        row["cash"] = cash
        row["fha_comparison"] = None
        if bid and bid > 0:
            a = fha_mod.resolve_assumptions(base, self.settings, row.get("county"), row.get("city"), 1)
            q = fha_mod.quote(bid, None, a)
            row["fha_comparison"] = {"price": bid, "piti": q.piti, "principal_interest": q.principal_interest,
                                     "down_payment": q.down_payment, "cash_to_close": q.cash_to_close, "rate": q.rate,
                                     "note": "Comparison only: PITI if this opening bid were financed like a purchase price (1-unit insurance)."}
        row["is_upcoming"] = bool(row.get("sale_date") and row["sale_date"] >= today)


def _f(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
