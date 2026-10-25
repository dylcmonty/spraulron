"""FastAPI application: two pages (Market, Auctions) and a small JSON API."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from .config import Settings, get_settings
from .finance import fha as fha_mod
from .service import Services

HERE = Path(__file__).resolve().parent


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    svc = Services(settings)
    app = FastAPI(title="spraulron", version=__version__)
    app.state.settings = settings
    app.state.services = svc
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    templates = Jinja2Templates(directory=str(HERE / "templates"))

    def page_config(kind: str) -> dict:
        f = settings.fha
        return {
            "kind": kind, "counties": settings.counties.names, "map": settings.map.model_dump(),
            "fha": {"down_payment_pct": f.down_payment_pct, "closing_cost_pct": f.closing_cost_pct,
                    "vacancy_repair_reserve_pct": f.vacancy_repair_reserve_pct, "market_rent_factor": f.market_rent_factor,
                    "target_cashflow": f.target_cashflow, "reserve_months": f.reserve_months_3_4_units,
                    "rate_fallback": f.rate_fallback, "rate_dpa_fallback": f.rate_dpa_fallback,
                    "tax_rate": f.tax_rate, "insurance_monthly": f.insurance_monthly, "utilities_monthly": f.utilities_monthly,
                    "loan_limits": f.loan_limits},
            "lookahead_days": settings.app.auction_lookahead_days,
            "has_rentcast": bool(settings.rentcast_api_key), "has_hud": bool(settings.hud_api_token),
        }

    @app.get("/", include_in_schema=False)
    def home():
        return RedirectResponse("/market")

    @app.get("/market", response_class=HTMLResponse)
    def market_page(request: Request):
        return templates.TemplateResponse(request, "market.html", {"page": page_config("market"), "page_json": json.dumps(page_config("market"))})

    @app.get("/auctions", response_class=HTMLResponse)
    def auctions_page(request: Request):
        return templates.TemplateResponse(request, "auctions.html", {"page": page_config("auctions"), "page_json": json.dumps(page_config("auctions"))})

    @app.get("/api/fha/defaults")
    def fha_defaults(request: Request):
        a, rates, rate_status = svc.assumptions_from_params(dict(request.query_params))
        return {"assumptions": a.model_dump(), "rates": rates, "rate_status": rate_status, "loan_limits": settings.fha.loan_limits}

    @app.get("/api/market")
    def api_market(request: Request, refresh: int = 0):
        a, rates, rate_status = svc.assumptions_from_params(dict(request.query_params))
        data = svc.market(a, force=bool(refresh))
        data["assumptions"] = a.model_dump()
        data["rates"] = rates
        data["sources"].insert(0, rate_status)
        return JSONResponse(data)

    @app.get("/api/auctions")
    def api_auctions(request: Request, refresh: int = 0, include_completed: int = 0):
        a, rates, rate_status = svc.assumptions_from_params(dict(request.query_params))
        data = svc.auctions(a, force=bool(refresh), include_completed=bool(include_completed))
        data["assumptions"] = a.model_dump()
        data["rates"] = rates
        data["sources"].insert(0, rate_status)
        return JSONResponse(data)

    @app.post("/api/refresh/{key}")
    def api_refresh(key: str):
        if key not in svc.source_specs():
            raise HTTPException(404, f"unknown source {key}")
        kw = {}
        if key == "realauction_cuyahoga":
            kw["docket_labels"] = svc.run("cuyahoga_docket").data or {}
        res = svc.run(key, force=True, **kw)
        return svc.status(key, res)

    @app.get("/api/quote")
    def api_quote(request: Request, price: float, units: Optional[int] = None, county: Optional[str] = None,
                  city: Optional[str] = None, tenant_rent: Optional[float] = None, all_units_rent: Optional[float] = None):
        a, _, _ = svc.assumptions_from_params(dict(request.query_params))
        a = fha_mod.resolve_assumptions(a, settings, county, city, units)
        limit = settings.fha.loan_limits.get(str(min(max(units or 1, 1), 4)))
        return fha_mod.quote(price, units, a, tenant_rent=tenant_rent, all_units_rent=all_units_rent, loan_limit=limit).model_dump()

    @app.get("/api/overrides")
    def get_overrides():
        return svc.load_overrides()

    @app.put("/api/overrides/{listing_id}")
    def put_override(listing_id: str, body: dict = Body(...)):
        allowed = {"units", "unit_beds", "rents", "notes", "starred", "hidden"}
        clean = {k: v for k, v in body.items() if k in allowed}
        if "units" in clean and clean["units"] not in (None, ""):
            clean["units"] = int(clean["units"])
        if "unit_beds" in clean and clean["unit_beds"]:
            clean["unit_beds"] = [int(x) if x not in (None, "") else None for x in clean["unit_beds"]]
        if "rents" in clean and clean["rents"]:
            clean["rents"] = [float(x) if x not in (None, "") else None for x in clean["rents"]]
        return svc.save_override(listing_id, clean)

    @app.delete("/api/overrides/{listing_id}")
    def delete_override(listing_id: str):
        return svc.save_override(listing_id, None)

    @app.get("/api/status")
    def api_status():
        return {"cache": svc.cache.listing(), "sources": svc.source_specs(), "version": __version__,
                "has_rentcast": bool(settings.rentcast_api_key), "has_hud": bool(settings.hud_api_token)}

    return app


app = create_app()
