"""RealAuction (RealForeclose) sheriff-sale sites used by Summit and Cuyahoga counties.

Flow (all plain HTTP, verified):
  1. GET index.cfm?zaction=USER&zmethod=CALENDAR&selCalDate=MM/DD/YYYY -> day cells with dayid + label.
  2. GET index.cfm?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE=MM/DD/YYYY (sets the date in the session).
  3. GET index.cfm?zaction=AUCTION&Zmethod=UPDATE&FNC=LOAD&AREA=W|C&PageDir=0&doR=0&tx=<ms>&bypassPage=N
     -> JSON {retHTML, rlist}; retHTML uses the token compression expanded in /CORE/System/JS/auction.js.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from ..models import AuctionItem
from .links import build_links, is_vacant_lot_text, money

COUNTIES = {
    "summit": {"base": "https://summit.sheriffsaleauction.ohio.gov", "county": "Summit", "label": "Summit County Sheriff (RealAuction)"},
    "cuyahoga": {"base": "https://cuyahoga.sheriffsaleauction.ohio.gov", "county": "Cuyahoga", "label": "Cuyahoga County Sheriff (RealAuction)"},
}

# Order matters: replicate auction.js exactly.
TOKENS = [
    ("@A", '<div class="'), ("@B", "</div>"), ("@C", 'class="'), ("@D", "<div>"), ("@E", "AUCTION"),
    ("@F", "</td><td"), ("@G", "</td></tr>"), ("@H", "<tr><td "), ("@I", "table"),
    ("@J", 'p_back="NextCheck='), ("@K", 'style="Display:none"'), ("@L", "/index.cfm?zaction=auction&zmethod=details&AID="),
]
_CAL_LABEL = re.compile(r"(?P<kind>[A-Za-z ]+?)\s+(?P<waiting>\d+)\s*/\s*(?P<total>\d+)\s+FC\s*(?P<time>[\d:]+\s*[AP]M\s*ET)?", re.I)
PAGE_SIZE = 10


def expand(ret_html: str) -> str:
    for tok, rep in TOKENS:
        ret_html = ret_html.replace(tok, rep)
    return ret_html


def parse_items(ret_html: str) -> list[dict]:
    soup = BeautifulSoup(expand(ret_html), "lxml")
    out: list[dict] = []
    for item in soup.find_all("div", class_=re.compile(r"^AUCTION_ITEM$")):
        rec: dict = {"aid": item.get("aid")}
        for tr in item.select("table.ad_tab tr"):
            th, td = tr.find("th"), tr.find("td")
            if th is None or td is None:
                continue
            key = th.get_text(" ", strip=True).rstrip(":").strip() or "Address2"
            rec[key] = td.get_text(" ", strip=True)
        for cls in ("ASTAT_MSGA", "ASTAT_MSGB", "ASTAT_MSGC", "ASTAT_MSGD", "ASTAT_MSG_SOLDTO_Label", "ASTAT_MSG_SOLDTO_MSG"):
            el = item.find(class_=cls)
            if el and el.get_text(strip=True):
                rec[cls] = el.get_text(" ", strip=True)
        out.append(rec)
    return out


def parse_calendar(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    days = []
    for cell in soup.find_all(attrs={"dayid": True}):
        text_el = cell.find(class_="CALTEXT")
        label = text_el.get_text(" ", strip=True) if text_el else ""
        m = _CAL_LABEL.search(label)
        try:
            d = datetime.strptime(cell["dayid"], "%m/%d/%Y").date()
        except ValueError:
            continue
        days.append({
            "date": d.isoformat(), "label": label,
            "kind": m.group("kind").strip() if m else None,
            "waiting": int(m.group("waiting")) if m else None,
            "total": int(m.group("total")) if m else None,
            "time": (m.group("time") or "").strip() or None if m else None,
        })
    return days


class RealAuctionClient:
    def __init__(self, client: httpx.Client, base: str):
        self.client = client
        self.base = base.rstrip("/")

    def calendar(self, d: date) -> list[dict]:
        r = self.client.get(f"{self.base}/index.cfm", params={"zaction": "USER", "zmethod": "CALENDAR", "selCalDate": d.strftime("%m/%d/%Y")})
        r.raise_for_status()
        return parse_calendar(r.text)

    def preview_url(self, d: date) -> str:
        return f"{self.base}/index.cfm?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE={d.strftime('%m/%d/%Y')}"

    def items_for_date(self, d: date, max_pages: int = 60) -> dict[str, list[dict]]:
        """Return {"W": [...waiting...], "C": [...closed/cancelled...]} raw records for a sale date."""
        pv = self.client.get(self.preview_url(d))
        pv.raise_for_status()
        result: dict[str, list[dict]] = {}
        for area in ("W", "C"):
            seen: set[str] = set()
            items: list[dict] = []
            for page in range(1, max_pages + 1):
                params = {"zaction": "AUCTION", "Zmethod": "UPDATE", "FNC": "LOAD", "AREA": area, "PageDir": 0,
                          "doR": 0, "tx": int(time.time() * 1000), "bypassPage": page}
                r = self.client.get(f"{self.base}/index.cfm", params=params,
                                    headers={"Referer": self.preview_url(d), "X-Requested-With": "XMLHttpRequest"})
                r.raise_for_status()
                try:
                    j = r.json()
                except ValueError:
                    break
                rlist = (j.get("rlist") or "").strip()
                if not rlist or rlist in seen:
                    break
                seen.add(rlist)
                items.extend(parse_items(j.get("retHTML", "")))
                if len(rlist.split(",")) < PAGE_SIZE:
                    break
            result[area] = items
        return result


def _split_city_zip(text: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not text:
        return None, None
    m = re.match(r"\s*(.*?)\s*,\s*(\d{5})\d*\s*$", text)
    if m:
        return m.group(1).strip().title(), m.group(2)
    return text.strip().title(), None


def classify(county_key: str, d: date, appraised: Optional[float], deposit: Optional[float],
             docket_labels: Optional[dict[str, str]]) -> str:
    """Sale type: Cuyahoga docket label when known, else appraisal rule, else county weekday pattern.

    Mortgage foreclosures always carry a court appraisal (opening bid = 2/3 of it); delinquent-tax sales show
    "Appraised Value: $0.00" with the tax balance as the minimum bid (verified on both counties' live data).
    """
    if docket_labels and d.isoformat() in docket_labels:
        return docket_labels[d.isoformat()]
    if appraised is not None and appraised == 0:
        return "Delinquent Tax"
    if appraised:
        return "Mortgage Foreclosure"
    tax_days = {"summit": {1}, "cuyahoga": {2}}.get(county_key, set())  # Summit Tue, Cuyahoga Wed
    return "Delinquent Tax" if d.weekday() in tax_days else "Mortgage Foreclosure"


def to_item(raw: dict, county_key: str, d: date, area: str, time_text: Optional[str], url: str,
            docket_labels: Optional[dict[str, str]]) -> AuctionItem:
    cfg = COUNTIES[county_key]
    city, zip_code = _split_city_zip(raw.get("Address2"))
    address = (raw.get("Property Address") or "").strip()
    appraised = money(raw.get("Appraised Value"))
    deposit = money(raw.get("Deposit Requirement"))
    opening = money(raw.get("Opening Bid"))
    case_status = raw.get("Case Status")
    status = case_status if area == "W" else f"Closed/Cancelled ({case_status})" if case_status else "Closed/Cancelled"
    sold_to = raw.get("ASTAT_MSG_SOLDTO_MSG")
    notes = " | ".join(v for v in (raw.get("ASTAT_MSGB"), raw.get("ASTAT_MSGD"), f"Sold to {sold_to}" if sold_to else None) if v) or None
    parcel = raw.get("Parcel ID")
    return AuctionItem(
        id=f"{county_key}-{raw.get('aid') or raw.get('Case #')}",
        source=f"realauction_{county_key}", source_label=cfg["label"], county=cfg["county"],
        sale_type=classify(county_key, d, appraised, deposit, docket_labels), sale_date=d, sale_time=time_text,
        status=status, address=address, city=city, zip=zip_code, parcel=parcel, case_number=raw.get("Case #"),
        appraised=appraised, opening_bid=opening, deposit=deposit, is_vacant_lot=is_vacant_lot_text(address),
        url=url, notes=notes, links=build_links(address, city, zip_code, cfg["county"], parcel, listing_url=url),
    )


def fetch(client: httpx.Client, settings, county_key: str, today: Optional[date] = None,
          docket_labels: Optional[dict[str, str]] = None, include_closed: bool = True) -> list[dict]:
    cfg = COUNTIES[county_key]
    today = today or date.today()
    horizon = today + timedelta(days=settings.app.auction_lookahead_days if settings else 75)
    ra = RealAuctionClient(client, cfg["base"])
    days: dict[str, dict] = {}
    month = today.replace(day=1)
    while month <= horizon:
        for day in ra.calendar(month):
            days[day["date"]] = day
        month = (month + timedelta(days=32)).replace(day=1)
    items: list[dict] = []
    for iso, day in sorted(days.items()):
        d = date.fromisoformat(iso)
        if d < today or d > horizon:
            continue
        areas = ra.items_for_date(d)
        for area, raws in areas.items():
            if area == "C" and not include_closed:
                continue
            for raw in raws:
                if not raw.get("aid"):
                    continue
                items.append(to_item(raw, county_key, d, area, day.get("time"), ra.preview_url(d), docket_labels).model_dump(mode="json"))
    return items
