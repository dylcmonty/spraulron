from spraulron.finance.rates import parse_ohfa
from spraulron.finance.rents import RentTable, distribute_bedrooms, estimate_units, parse_hud_payload, rent_totals


def test_parse_ohfa_rates(fixture_text):
    r = parse_ohfa(fixture_text("ohfa_rates_2026-10-23.html"))
    assert r["fha_no_dpa"] == 0.07375
    assert r["fha_dpa"] == 0.08
    assert r["conv_no_dpa"] == 0.07625
    assert r["updated"].startswith("10/23/26")


def test_distribute_bedrooms():
    assert distribute_bedrooms(3, 7) == [3, 2, 2]
    assert distribute_bedrooms(4, 8) == [2, 2, 2, 2]
    assert distribute_bedrooms(2, None) == [None, None]


def test_estimate_units_owner_takes_lowest():
    table = RentTable(source="HUD FMR API", county_fmr={"Cuyahoga": [800, 950, 1150, 1400, 1600]}, zip_fmr={"44102": [850, 1000, 1200, 1450, 1650]})
    units = estimate_units(3, 7, "44102", "Cuyahoga", table, 1.0)
    rents = [u.rent for u in units]
    assert rents == [1450, 1200, 1200]
    tenant, total = rent_totals(units)
    assert total == 3850 and tenant == 2650
    assert sum(u.owner_occupied for u in units) == 1
    ov = estimate_units(3, 7, "44102", "Cuyahoga", table, 1.0, overrides={"rents": [1500, None, 900]})
    assert ov[0].rent == 1500 and ov[0].rent_source == "override" and ov[2].rent == 900


def test_parse_hud_payload_shapes():
    county_row, zips, year = parse_hud_payload({"data": {"year": "2026", "basicdata": {"Efficiency": 700, "One-Bedroom": 800, "Two-Bedroom": 1000, "Three-Bedroom": 1300, "Four-Bedroom": 1500}}}, "Stark")
    assert county_row == [700, 800, 1000, 1300, 1500] and year == "2026" and zips == {}
    county_row, zips, _ = parse_hud_payload({"data": {"basicdata": [
        {"zip_code": "44102", "Efficiency": 800, "One-Bedroom": 900, "Two-Bedroom": 1100, "Three-Bedroom": 1400, "Four-Bedroom": 1500},
        {"zip_code": "44109", "Efficiency": 820, "One-Bedroom": 920, "Two-Bedroom": 1120, "Three-Bedroom": 1420, "Four-Bedroom": 1520}]}}, "Cuyahoga")
    assert set(zips) == {"44102", "44109"} and county_row[2] == 1110
