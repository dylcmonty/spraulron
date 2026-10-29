"""CLI: serve the site, refresh sources, run an FHA quote, show cache status."""
from __future__ import annotations

import argparse
import json
import sys

from .config import get_settings


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="spraulron", description="NE Ohio multifamily market + auction tracker")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the local website")
    s.add_argument("--host"); s.add_argument("--port", type=int); s.add_argument("--reload", action="store_true")
    r = sub.add_parser("refresh", help="fetch sources now (bypassing the cache)")
    r.add_argument("sources", nargs="*", help="source keys (default: all)"); r.add_argument("--no-force", action="store_true")
    q = sub.add_parser("fha", help="print an FHA quote")
    q.add_argument("price", type=float); q.add_argument("units", type=int); q.add_argument("--rent", type=float, help="monthly tenant rent")
    q.add_argument("--all-rent", type=float); q.add_argument("--county"); q.add_argument("--city"); q.add_argument("--rate", type=float); q.add_argument("--dpa", action="store_true")
    sub.add_parser("status", help="show cache files and source config")
    sub.add_parser("prune", help="delete cache files older than app.cache_prune_days")
    args = p.parse_args(argv)
    settings = get_settings()

    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("spraulron.app:app", host=args.host or settings.app.host, port=args.port or settings.app.port, reload=args.reload)
        return 0

    from .service import Services
    svc = Services(settings)
    if args.cmd == "refresh":
        keys = args.sources or list(svc.source_specs())
        if "rentcast" in keys and not settings.rentcast_api_key and not args.sources:
            keys.remove("rentcast")
        if "hud_fmr" in keys and not settings.hud_api_token and not args.sources:
            keys.remove("hud_fmr")
        labels = None
        for key in keys:
            kw = {}
            if key == "realauction_cuyahoga":
                labels = labels or (svc.run("cuyahoga_docket").data or {})
                kw["docket_labels"] = labels
            res = svc.run(key, force=not args.no_force, **kw)
            st = svc.status(key, res)
            n = st["count"] if st["count"] is not None else ("dict" if isinstance(res.data, dict) else "-")
            print(f"{key:22} {('STALE ' if st['stale'] else '')}{'cached' if st['from_cache'] else 'fresh ':6} rows={n} {('ERROR: ' + st['error']) if st['error'] else ''}")
        return 0
    if args.cmd == "fha":
        from .finance import fha as fha_mod
        a, rates, _ = svc.assumptions_from_params({"rate": args.rate, "dpa": "1" if args.dpa else "0"})
        a = fha_mod.resolve_assumptions(a, settings, args.county, args.city, args.units)
        qq = fha_mod.quote(args.price, args.units, a, tenant_rent=args.rent, all_units_rent=args.all_rent,
                           loan_limit=settings.fha.loan_limits.get(str(min(max(args.units, 1), 4))))
        print(json.dumps(qq.model_dump(), indent=1))
        return 0
    if args.cmd == "status":
        print(json.dumps({"cache": svc.cache.listing(), "has_rentcast": bool(settings.rentcast_api_key), "has_hud": bool(settings.hud_api_token)}, indent=1))
        return 0
    if args.cmd == "prune":
        print(f"removed {svc.cache.prune(settings.app.cache_prune_days)} cache files")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
