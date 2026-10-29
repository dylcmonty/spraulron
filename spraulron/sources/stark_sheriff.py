"""Stark County Sheriff sales (own ASP.NET site; needs a session cookie from the list page first)."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from ..models import AuctionItem
from .links import build_links, is_vacant_lot_text, money

KEY = "stark_sheriff"
LABEL = "Stark County Sheriff"
BASE = "https://sheriffsales.starkcountyohio.gov/"
KINDS = {
    "foreclosure": {"list": "Foreclosures.aspx", "detail": "ForeclosureSaleDetails.aspx", "sale_type": "Mortgage Foreclosure"},
    "delinquent": {"list": "DelinquentTaxSales.aspx", "detail": "DelinquentTaxSaleDetails.aspx", "sale_type": "Delinquent Tax"},
}
_DATE = re.compile(r"SALE_DATE=(\d{1,2}/\d{1,2}/\d{4})")


def parse_sale_dates(html: str) -> dict[str, list[str]]:
    soup = BeautifulSoup(html, "lxml")
    out = {"pending": [], "completed": []}
    for bucket, tid in (("pending", "_ctl0_ContentPlaceHolder1_dlPendingSales"), ("completed", "_ctl0_ContentPlaceHolder1_dlCompletedSales")):
        t = soup.find(id=tid)
        if not t:
            continue
        for a in t.find_all("a", href=True):
            m = _DATE.search(a["href"])
            if m:
                out[bucket].append(datetime.strptime(m.group(1), "%m/%d/%Y").date().isoformat())
    return out


def _cells(table) -> dict[str, str]:
    rec: dict[str, str] = {}
    for td in table.find_all("td"):
        label = td.find("span", class_="titleStyle")
        if not label:
            continue
        key = label.get_text(" ", strip=True).rstrip(":").strip()
        value = td.get_text(" ", strip=True)
        value = value[len(label.get_text(" ", strip=True)):].strip() if value.startswith(label.get_text(" ", strip=True)) else value
        rec[key] = value
    return rec


def _parse_address(addr: str) -> tuple[str, Optional[str], Optional[str]]:
    """'328 N. WEBB AVENUE, ALLIANCE, OHO 44601' -> (street, city, zip)."""
    a = addr.strip()
    zip_code = None
    m = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", a)
    if m:
        zip_code = m.group(1)
        a = a[: m.start()].strip()
    a = re.sub(r"[,\s]+(OHIO|OHO|OH)\s*$", "", a, flags=re.I).strip(" ,")
    city = None
    parts = [p.strip() for p in a.split(",") if p.strip()]
    if len(parts) >= 2:
        city = parts[-1].title()
        street = ", ".join(parts[:-1])
    else:
        street = a
    return street, city, zip_code


def parse_details(html: str, kind: str, sale_date: date) -> list[dict]:
    """One record per property on the page. An expanded ("Details") row carries the full address, plaintiff, attorney."""
    soup = BeautifulSoup(html, "lxml")
    outer = soup.find(id="_ctl0_ContentPlaceHolder1_dlSales")
    if not outer:
        return []
    recs = []
    cells = [td for td in outer.find_all("td", class_=re.compile(r"detailStyle|seletedDataStyle|selectedDataStyle"))]
    for i, td in enumerate(cells):
        inner = td.find("table", recursive=False)
        if inner is None:
            continue
        rec = _cells(inner)
        if not rec:
            continue
        if "detailStyle" not in (td.get("class") or []):
            rec["_expanded"] = True
            b = inner.find("b", string=re.compile("Reason Not Sold"))
            if b:
                rec["Reason Not Sold"] = b.parent.get_text(" ", strip=True).replace("*Reason Not Sold:", "").strip()
        btn = inner.find("input", {"type": "submit"})
        rec["_button"] = btn.get("name") if btn else None
        rec["_index"] = i
        recs.append(rec)
    return recs


def form_fields(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form")
    if not form:
        return {}
    return {i["name"]: i.get("value", "") for i in form.find_all("input", {"type": "hidden"}) if i.get("name")}


def has_next_page(html: str) -> bool:
    soup = BeautifulSoup(html, "lxml")
    nxt = soup.find(id="_ctl0_ContentPlaceHolder1_btnNextPage")
    return bool(nxt and nxt.name == "a" and nxt.get("href"))


def to_item(rec: dict, kind: str, sale_date: date) -> AuctionItem:
    raw_addr = rec.get("Address", "")
    truncated = raw_addr.endswith("...") and not rec.get("_expanded")
    street, city, zip_code = _parse_address(raw_addr.rstrip(".") if truncated else raw_addr)
    city = rec.get("City") or city
    if truncated:
        street = raw_addr.rstrip(".").split(",")[0].strip()
    high = rec.get("High Bid")
    high_bid = money(high) if high and "not sold" not in high.lower() else None
    status = "Pending" if sale_date >= date.today() else ("Sold" if high_bid else (high or "Completed"))
    case = rec.get("Case #")
    parcel = rec.get("Parcels")
    url = f"{BASE}{KINDS[kind]['detail']}?SALE_DATE={sale_date.month}/{sale_date.day}/{sale_date.year}"
    pvd = rec.get("Plaintiff vs. Defendant")
    if pvd:
        pvd = re.sub(r"\s*vs\.\s*", " vs. ", pvd).strip(" ;")
    extra = [x for x in (f"Reason not sold: {rec['Reason Not Sold']}" if rec.get("Reason Not Sold") else None,
                         f"Attorney: {rec['Attorney']}" if rec.get("Attorney") else None) if x]
    return AuctionItem(
        id=f"stark-{kind}-{case}-{sale_date.isoformat()}", source=KEY, source_label=LABEL, county="Stark",
        sale_type=KINDS[kind]["sale_type"], sale_date=sale_date, sale_time="10:00 AM",
        status=status, address=street or raw_addr, city=city, zip=zip_code, parcel=parcel, case_number=case,
        appraised=money(rec.get("Appraised")), opening_bid=money(rec.get("Start Bid")), high_bid=high_bid,
        is_vacant_lot=is_vacant_lot_text(raw_addr), url=url, plaintiff_defendant=pvd,
        notes=" | ".join((["address truncated on county list"] if truncated else []) + extra) or None,
        links=build_links(street or raw_addr, city, zip_code, "Stark", parcel, listing_url=url),
    )


def _page_records(client: httpx.Client, url: str, html: str, kind: str, d: date, expand: bool, budget: list[int]) -> tuple[list[dict], str]:
    """Parse one page; expand truncated rows via the per-row "Details" postback (one POST each)."""
    recs = parse_details(html, kind, d)
    if expand:
        for i, rec in enumerate(recs):
            if budget[0] <= 0:
                break
            if rec.get("Address", "").endswith("...") and rec.get("_button") and not rec.get("_expanded"):
                data = form_fields(html)
                data[rec["_button"]] = "Details"
                try:
                    r = client.post(url, data=data, headers={"Referer": url})
                    r.raise_for_status()
                except Exception:
                    continue
                budget[0] -= 1
                html = r.text
                new = parse_details(html, kind, d)
                exp = next((x for x in new if x.get("_expanded") and x.get("Case #") == rec.get("Case #")), None)
                if exp:
                    recs[i] = exp
    return recs, html


def fetch(client: httpx.Client, settings=None, include_completed: bool = False, completed_limit: int = 3,
          expand_details: bool = True, postback_budget: int = 150, max_pages: int = 20) -> list[dict]:
    items: list[dict] = []
    budget = [postback_budget]
    for kind, cfg in KINDS.items():
        lst = client.get(BASE + cfg["list"])
        lst.raise_for_status()
        dates = parse_sale_dates(lst.text)
        wanted = list(dates["pending"])
        if include_completed:
            wanted += sorted(dates["completed"], reverse=True)[:completed_limit]
        for iso in wanted:
            d = date.fromisoformat(iso)
            url = f"{BASE}{cfg['detail']}?SALE_DATE={d.month}/{d.day}/{d.year}"
            r = client.get(url, headers={"Referer": BASE + cfg["list"]})
            r.raise_for_status()
            if "Session Expired" in r.text:
                client.get(BASE + cfg["list"])
                r = client.get(url)
            html = r.text
            seen_cases: set[str] = set()
            for _page in range(max_pages):
                recs, html = _page_records(client, url, html, kind, d, expand_details, budget)
                for rec in recs:
                    key = f"{rec.get('Case #')}|{rec.get('Parcels')}"
                    if key in seen_cases:
                        continue
                    seen_cases.add(key)
                    items.append(to_item(rec, kind, d).model_dump(mode="json"))
                if not has_next_page(html):
                    break
                data = form_fields(html)
                data["__EVENTTARGET"] = "_ctl0$ContentPlaceHolder1$btnNextPage"
                data["__EVENTARGUMENT"] = ""
                nxt = client.post(url, data=data, headers={"Referer": url})
                nxt.raise_for_status()
                if nxt.text == html:
                    break
                html = nxt.text
    return items
