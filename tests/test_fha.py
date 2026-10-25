from datetime import date

from spraulron.finance import fha
from spraulron.models import FhaAssumptions


def base(**kw):
    a = dict(rate=0.07375, down_payment_pct=0.035, tax_rate=0.0229, insurance_monthly=250, utilities_monthly=125,
             closing_cost_pct=0.03, reserve_months=3, vacancy_repair_reserve_pct=0.20)
    a.update(kw)
    return FhaAssumptions(**a)


def test_200k_triplex_matches_prior_analysis():
    q = fha.quote(200_000, 3, base())
    assert abs(q.down_payment - 7_000) < 1
    assert abs(q.principal_interest - 1_356) < 5
    assert abs(q.mip_monthly - 88) < 2
    assert abs(q.piti - 2_076) < 5
    assert q.reserves_required > 0 and abs(q.reserves_required - 3 * q.piti) < 0.01


def test_five_percent_down_lowers_mip():
    q35 = fha.quote(200_000, 3, base())
    q5 = fha.quote(200_000, 3, base(down_payment_pct=0.05))
    assert q5.mip_monthly < q35.mip_monthly
    assert abs(q5.principal_interest - 1_335) < 5
    assert abs(q5.piti - 2_046) < 6


def test_breakeven_price_fourplex_matches_table():
    # prior analysis: fourplex, $1,100/unit x 3 tenants, 2.3% tax, $250 ins, $175 utilities -> ~$242k break-even
    a = base(tax_rate=0.023, insurance_monthly=250, utilities_monthly=175)
    q = fha.quote(242_000, 4, a, tenant_rent=3_300, all_units_rent=4_400)
    assert abs(q.break_even_price - 242_000) < 3_000
    assert abs(q.cash_flow) < 40
    assert q.target_price < q.break_even_price


def test_self_sufficiency_flag():
    a = base()
    ok = fha.quote(150_000, 3, a, tenant_rent=2_400, all_units_rent=3_400)
    bad = fha.quote(250_000, 3, a, tenant_rent=1_800, all_units_rent=2_400)
    assert ok.self_sufficiency_pass is True
    assert bad.self_sufficiency_pass is False and "fails FHA self-sufficiency test" in bad.flags
    duplex = fha.quote(150_000, 2, a, tenant_rent=1_100, all_units_rent=2_200)
    assert duplex.self_sufficiency_pass is None
    assert duplex.reserves_required == 0


def test_resolve_assumptions_uses_county_and_city(settings):
    a = FhaAssumptions(rate=0.07)
    r = fha.resolve_assumptions(a, settings, "Cuyahoga", "Cleveland Heights", 4)
    assert r.tax_rate == settings.fha.tax_rate_city["Cleveland Heights"]
    assert r.insurance_monthly == settings.fha.insurance_monthly["4"]
    r2 = fha.resolve_assumptions(a, settings, "Stark", None, 2)
    assert r2.tax_rate == settings.fha.tax_rate["Stark"]
