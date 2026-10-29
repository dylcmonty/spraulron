from datetime import date

from spraulron.sources import cuyahoga_docket, cuyahoga_forfeited, cuyahoga_landbank, realauction, stark_sheriff
from spraulron.sources.links import build_links, is_vacant_lot_text, money
from spraulron.sources.market_homeharvest import parse_units_from_text, to_listing, assume_units


def test_realauction_token_expansion_and_parse(fixture_json):
    j = fixture_json("realauction_summit_W_2026-10-30.json")
    items = realauction.parse_items(j["retHTML"])
    assert len(items) == len(j["rlist"].split(","))
    first = items[0]
    assert first["aid"] == "56536"
    assert first["Case #"] == "CV2023020546" and first["Parcel ID"] == "6760847"
    assert first["Property Address"] == "406 SKYLARK DRIVE" and first["Address2"] == "AKRON , 44313"
    assert first["Appraised Value"] == "$399,000.00" and first["Opening Bid"] == "$266,000.00" and first["Deposit Requirement"] == "$10,000.00"
    item = realauction.to_item(first, "summit", date(2026, 10, 30), "W", "10:00 AM ET", "http://x", None)
    assert item.city == "Akron" and item.zip == "44313" and item.opening_bid == 266000 and item.sale_type == "Mortgage Foreclosure"
    assert item.links["zillow"].startswith("https://www.zillow.com/homes/")


def test_realauction_cuyahoga_zip_padding_and_labels(fixture_json, fixture_text):
    j = fixture_json("realauction_cuyahoga_W_2026-10-26.json")
    items = realauction.parse_items(j["retHTML"])
    assert len(items) == 10
    labels = cuyahoga_docket.parse_labels(fixture_text("cuyahoga_docket_search.html"))
    assert labels["2026-10-21"] == "Delinquent Tax" and labels["2026-10-19"] == "Mortgage Foreclosure"
    it = realauction.to_item(items[0], "cuyahoga", date(2026, 10, 26), "W", None, "http://x", labels)
    assert it.zip == "44137" and it.city == "Maple Heights"
    tax = realauction.classify("cuyahoga", date(2026, 10, 21), 0.0, 5000.0, labels)
    assert tax == "Delinquent Tax"


def test_realauction_calendar(fixture_text):
    days = realauction.parse_calendar(fixture_text("realauction_summit_calendar_2026-10.html"))
    assert any(d["date"] == "2026-10-30" and d["waiting"] == 6 and d["total"] == 10 for d in days)
    assert all(d["time"] for d in days)


def test_stark_lists_and_details(fixture_text):
    dates = stark_sheriff.parse_sale_dates(fixture_text("stark_foreclosures_list.html"))
    assert "2026-10-26" in dates["pending"] and len(dates["completed"]) > 10
    recs = stark_sheriff.parse_details(fixture_text("stark_foreclosure_details_2026-10-26.html"), "foreclosure", date(2026, 10, 26))
    assert len(recs) == 3
    item = stark_sheriff.to_item(recs[0], "foreclosure", date(2026, 10, 26))
    assert item.case_number == "2026CV00187" and item.appraised == 108000 and item.opening_bid == 72000
    assert item.city == "Massillon" and item.parcel == "06-07894" and item.address.startswith("710 CHERRY RD NE")
    dt = stark_sheriff.parse_details(fixture_text("stark_delinquent_details_2026-11-03.html"), "delinquent", date(2026, 11, 3))
    assert len(dt) == 10
    it2 = stark_sheriff.to_item(dt[1], "delinquent", date(2026, 11, 3))
    assert it2.zip == "44601" and it2.city == "Alliance" and it2.opening_bid == 11569.15
    assert stark_sheriff.to_item(dt[0], "delinquent", date(2026, 11, 3)).is_vacant_lot


def test_cuyahoga_landbank_cards(fixture_text):
    cards = cuyahoga_landbank.parse_cards(fixture_text("cuyahoga_landbank_cards.html"))
    assert len(cards) == 4
    item = cuyahoga_landbank.to_item(cards[0])
    assert item.photo and item.photo.startswith("https://storage.googleapis.com/")
    assert item.parcel and item.city and item.zip and item.sale_type == "Land Bank"


def test_cuyahoga_forfeited_feature(fixture_json):
    feats = fixture_json("cuyahoga_forfeited_sample.json")["features"]
    item = cuyahoga_forfeited.to_item(feats[0])
    assert item.county == "Cuyahoga" and item.parcel and item.address and item.lat and item.lon
    assert item.links["county_parcel"].endswith(f"parcelid={item.parcel[:3]}-{item.parcel[3:5]}-{item.parcel[5:]}")


def test_units_from_text_and_homeharvest_mapping():
    assert parse_units_from_text("Charming duplex with two 2-bed units") == 2
    assert parse_units_from_text("Fully rented 4 unit building") == 4
    assert parse_units_from_text("Triplex near downtown") == 3
    assert parse_units_from_text("single family home") is None
    assert parse_units_from_text("Turnkey all-brick four-unit in Hough") == 4
    assert parse_units_from_text("Both units are rented month to month") == 2
    assert parse_units_from_text("Unit 1 features 3 bedrooms. Unit 2 has 2. Unit 3 is an efficiency.") == 3
    assert parse_units_from_text("Updated 2nd floor unit; 1st floor unit is vacant") == 2
    assert parse_units_from_text("Great double in Old Brooklyn") == 2
    assert parse_units_from_text("Colonial on a double lot with a double garage") is None
    assert parse_units_from_text("3 bedroom unit over retail") is None
    assert assume_units(None, 3.0) == (3, "3 baths")
    assert assume_units(8, None) == (4, "8 beds")
    assert assume_units(2, 1.0) == (2, "1 baths")
    assert assume_units(None, None) == (2, "default")
    p = {"property_url": "https://www.realtor.com/x", "property_id": "123", "list_price": 199900, "county": "Cuyahoga County",
         "address": {"full_line": "8604 Willard Ave", "city": "Cleveland", "state": "OH", "zip": "44102"},
         "description": {"beds": 5, "baths_full": 3, "sqft": 2400, "year_built": 1910, "text": "Triplex with three units", "primary_photo": "https://img/1.jpg", "alt_photos": ["https://img/2.jpg"], "type": "multi_family"},
         "latitude": 41.48, "longitude": -81.75, "days_on_mls": 12, "tax": 2900}
    l = to_listing(p)
    assert l.units == 3 and l.units_source == "text" and l.county == "Cuyahoga" and l.photo == "https://img/1.jpg"
    assert l.tax_annual == 2900 and l.links["source"] == "https://www.realtor.com/x"


def test_sold_stats_and_price_range():
    from spraulron.sources.market_homeharvest import sold_stats_from_records
    from spraulron.service import price_range
    recs = [{"list_price": 100000, "sold_price": 95000 + 1000 * i, "text": "duplex", "beds": 4, "full_baths": 2} for i in range(10)]
    recs += [{"list_price": 200000, "sold_price": 190000, "text": "triplex"}, {"list_price": 50000, "sold_price": 1000}]  # ratio outlier dropped
    st = sold_stats_from_records(recs)
    assert st["ratio"]["n"] == 11 and 0.9 < st["ratio"]["mean"] < 1.0
    assert st["per_unit"]["2"]["n"] == 10 and st["per_unit"]["3"]["n"] == 1
    pr = price_range(120000, 2, st, past_days=180)
    assert pr["sale"]["low"] < pr["sale"]["mid"] < pr["sale"]["high"] and pr["sale"]["n"] == 11
    assert pr["comps"]["basis"] == "2-unit" and pr["comps"]["low"] < pr["comps"]["mid"] < pr["comps"]["high"]
    assert "log_mean" in st["per_unit"]["2"]
    assert price_range(120000, 3, st)["comps"]["basis"] == "all multifamily"
    assert price_range(120000, 2, {}) is None


def test_helpers():
    assert money("$26,661.39") == 26661.39 and money("Not Sold") is None
    assert is_vacant_lot_text("VACANT LOT 3729 EAST 146TH ST") and not is_vacant_lot_text("406 SKYLARK DRIVE")
    links = build_links("406 Skylark Dr", "Akron", "44313", "Summit", "6760847", 41.13, -81.52)
    assert "google_maps" in links and "street_view" in links and links["county_parcel"].startswith("https://fiscaloffice")
