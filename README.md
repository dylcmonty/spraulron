# spraulron

Locally hosted website that keeps an up-to-date view of **2–4 unit multifamily homes for sale** and **properties at auction** in
Summit, Cuyahoga and Stark counties (Ohio), with **FHA / OHFA first-time-homebuyer math** applied to every row.

Two pages, nothing else:

| Page | What it shows | Where the data comes from (live, short-lived cache) |
|---|---|---|
| `/market` | For-sale duplex/triplex/fourplex listings with photos, estimated rent per unit, FHA down payment, PITI, cash-to-close, cash flow, break-even price and the FHA self-sufficiency test | Realtor.com via [HomeHarvest](https://pypi.org/project/homeharvest/) (default), [RentCast API](https://www.rentcast.io/api) (optional key), HUD Fair Market Rents, OHFA daily rates |
| `/auctions` | Sheriff sales (delinquent-tax + mortgage foreclosure), forfeited-land parcels and land-bank inventory with opening bid, deposit, cash reality and an "if financed" FHA comparison | RealAuction (Summit, Cuyahoga), Stark County Sheriff, Cuyahoga Clerk docket (sale-type labels), Cuyahoga forfeited-land GIS layer, Cuyahoga Land Bank |

Design rule: **don't warehouse data.** Every source is fetched on demand through a TTL JSON cache in `.cache/` (gitignored),
images are hot-linked from their source, and the only things persisted are your per-listing overrides (`data/overrides.json`)
and a geocode cache (`data/geocache.json`).

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # optional: add RENTCAST_API_KEY and HUD_API_TOKEN
.venv/bin/python -m spraulron serve   # http://127.0.0.1:8000
```

Other commands:

```bash
.venv/bin/python -m spraulron refresh                 # fetch every source now, print row counts
.venv/bin/python -m spraulron refresh realauction_summit stark_sheriff
.venv/bin/python -m spraulron fha 200000 3 --rent 2300 --county Cuyahoga --city Cleveland
.venv/bin/python -m spraulron status | prune
.venv/bin/python -m pytest
```

## Tokens (optional)

* `HUD_API_TOKEN` — HUD USER Fair Market Rent API (https://www.huduser.gov/portal/dataset/fmr-api.html). Without it the app uses
  the rough estimates in `data/fmr_fallback.json` and says so in a banner.
* `RENTCAST_API_KEY` — RentCast developer plan (50 requests/month; RentCast asks for a credit card at signup even on the free
  plan). The adapter is off unless the key is present and runs one radius query per refresh, cached 24 h.

## FHA model

Defaults live in `config.toml` and can be changed in the browser (saved in your browser's local storage):
OHFA First-Time Homebuyer FHA rate scraped daily from https://ohiohome.org/rates/ (toggle for the 3.5% DPA rate),
3.5% down, 1.75% UFMIP financed, 0.55% annual MIP (0.50% at ≥5% down), county/city effective tax rate (estimates: verify per parcel),
insurance and shared-utility allowances by unit count, 20% vacancy + repair reserve, 2–5% closing costs, three months of PITI
reserves for 3–4 units, owner occupies the lowest-rent unit. The self-sufficiency test is 75% of all-unit market rent ≥ PITI.
Sheriff sales are shown with deposit + 30-day balance; the FHA number there is a comparison, not a financing option.

## Sources and terms

County and HUD/OHFA pages are public records fetched with ordinary HTTP at low volume. HomeHarvest is an unofficial
Realtor.com client; keep refresh intervals modest and treat it as a convenience that may break. RentCast's API terms allow
local storage and display. Nothing here is underwriting, legal advice or a title search.

## Filters and ranges

Filters act on three things only: price, projected rent per unit and the FHA down payment needed. Everything in the
FHA panel (rate, DPA, down %, closing, reserves, rent factor, target, tax/insurance/utilities overrides) feeds the computed
columns and never hides a row. Opening a row shows ±1 standard-deviation bands: the likely sale price (asking × the sale÷list
ratio of recent multifamily sales in that county, and sold $/unit comparables for the same unit count), both from Realtor.com
sales over the last `sold_past_days` with only the aggregates cached, and the rent band per unit from the spread of HUD ZIP-level
FMRs for that bedroom count (`rent_rel_sd_fallback` when no ZIP data exists). Table cells show the plain list price and the
projected rent per unit.

## Unit counts

Realtor.com rarely states the unit count, so it is parsed from the description (duplex, four-unit, "Cleveland double",
"both units", "Unit 1 … Unit 3", "2nd floor unit"). When nothing matches, the app assumes one unit per bathroom (else beds ÷ 2,
clamped to 2–4) so rent and cash flow still show; those rows carry a `2?`-style badge and sit in the "unknown / assumed" filter.
Set the real count with ✎ and the projection recomputes.

## Layout

```
spraulron/            FastAPI app, service layer, cache, models, CLI
  finance/            fha.py (pure math), rates.py (OHFA), rents.py (HUD FMR + per-unit estimates)
  sources/            one module per data source + links.py (outbound URLs)
  templates/ static/  two pages, one stylesheet, one script (Leaflet map from CDN)
tests/                parser tests on captured fixtures + FHA math checks
```
