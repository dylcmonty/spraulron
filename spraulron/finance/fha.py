"""FHA / OHFA first-time-homebuyer math. Pure functions, no I/O.

Model (matches the user's prior analysis):
  down = price * d; base_loan = price - down; UFMIP financed; annual MIP on base loan;
  PITI = P&I + MIP + tax + insurance; owner lives in the lowest-rent unit;
  cash_flow = tenant_rent - reserve% * tenant_rent - PITI - shared utilities.
"""
from __future__ import annotations

from typing import Optional

from ..config import Settings
from ..models import FhaAssumptions, FhaQuote


def monthly_payment(principal: float, annual_rate: float, months: int) -> float:
    if principal <= 0:
        return 0.0
    r = annual_rate / 12.0
    if r <= 0:
        return principal / months
    return principal * r / (1 - (1 + r) ** (-months))


def annual_mip_rate(a: FhaAssumptions) -> float:
    return a.annual_mip_pct_5pct_down if a.down_payment_pct >= 0.05 - 1e-9 else a.annual_mip_pct


def resolve_assumptions(base: FhaAssumptions, settings: Settings, county: Optional[str], city: Optional[str],
                        units: Optional[int]) -> FhaAssumptions:
    """Fill county/city tax rate and unit-based insurance/utilities when the caller left them None."""
    data = base.model_dump()
    if data.get("tax_rate") is None:
        data["tax_rate"] = settings.tax_rate_for(county, city)
    if data.get("insurance_monthly") is None:
        data["insurance_monthly"] = settings.insurance_for(units)
    if data.get("utilities_monthly") is None:
        data["utilities_monthly"] = settings.utilities_for(units)
    return FhaAssumptions(**data)


def _price_coefficient(a: FhaAssumptions) -> float:
    """Monthly cost per $1 of purchase price (P&I + MIP + tax), used to invert cash flow -> price."""
    f = monthly_payment(1.0, a.rate, a.term_months)
    d = a.down_payment_pct
    return (1 - d) * (1 + a.ufmip_pct) * f + (1 - d) * annual_mip_rate(a) / 12 + (a.tax_rate or 0) / 12


def max_price_for_cashflow(a: FhaAssumptions, tenant_rent: float, target_cashflow: float) -> Optional[float]:
    coef = _price_coefficient(a)
    if coef <= 0:
        return None
    fixed = (a.insurance_monthly or 0) + (a.utilities_monthly or 0)
    p = (tenant_rent * (1 - a.vacancy_repair_reserve_pct) - fixed - target_cashflow) / coef
    return max(p, 0.0)


def quote(price: float, units: Optional[int], a: FhaAssumptions, *, tenant_rent: Optional[float] = None,
          all_units_rent: Optional[float] = None, tax_annual: Optional[float] = None,
          loan_limit: Optional[float] = None) -> FhaQuote:
    if a.tax_rate is None or a.insurance_monthly is None or a.utilities_monthly is None:
        raise ValueError("assumptions must be resolved (tax_rate, insurance_monthly, utilities_monthly)")
    flags: list[str] = []
    down = price * a.down_payment_pct
    base_loan = price - down
    ufmip = base_loan * a.ufmip_pct
    loan = base_loan + ufmip
    pi = monthly_payment(loan, a.rate, a.term_months)
    mip = base_loan * annual_mip_rate(a) / 12
    if tax_annual and tax_annual > 0:
        tax = tax_annual / 12
        tax_rate_used = tax_annual / price if price else a.tax_rate
        flags.append("tax from listing")
    else:
        tax = price * a.tax_rate / 12
        tax_rate_used = a.tax_rate
    ins = a.insurance_monthly
    piti = pi + mip + tax + ins
    closing = price * a.closing_cost_pct
    cash_to_close = down + closing
    reserves = piti * a.reserve_months if (units or 0) >= 3 else 0.0
    total_cash = cash_to_close + reserves

    q = FhaQuote(
        price=price, units=units, rate=a.rate, down_payment_pct=a.down_payment_pct, down_payment=down,
        base_loan=base_loan, ufmip=ufmip, loan_amount=loan, principal_interest=pi, mip_monthly=mip,
        tax_monthly=tax, tax_rate_used=tax_rate_used, insurance_monthly=ins, piti=piti,
        utilities_monthly=a.utilities_monthly, closing_costs=closing, cash_to_close=cash_to_close,
        reserves_required=reserves, total_cash_needed=total_cash, target_cashflow=a.target_cashflow,
    )
    if tenant_rent is not None:
        q.tenant_rent = tenant_rent
        q.reserve_allowance = tenant_rent * a.vacancy_repair_reserve_pct
        q.cash_flow = tenant_rent - q.reserve_allowance - piti - a.utilities_monthly
        q.break_even_price = max_price_for_cashflow(a, tenant_rent, 0.0)
        q.target_price = max_price_for_cashflow(a, tenant_rent, a.target_cashflow)
    if all_units_rent is not None:
        q.all_units_rent = all_units_rent
        if (units or 0) >= 3:
            q.self_sufficiency_rent = all_units_rent * a.self_sufficiency_factor
            q.self_sufficiency_pass = q.self_sufficiency_rent >= piti
            if not q.self_sufficiency_pass:
                flags.append("fails FHA self-sufficiency test")
    if loan_limit:
        q.within_loan_limit = loan <= loan_limit
        if not q.within_loan_limit:
            flags.append("exceeds FHA loan limit")
    q.flags = flags
    return q


def default_assumptions(settings: Settings, rate: Optional[float] = None, with_dpa: bool = False) -> FhaAssumptions:
    f = settings.fha
    if rate is None:
        rate = f.rate_dpa_fallback if with_dpa else f.rate_fallback
    return FhaAssumptions(
        rate=rate, down_payment_pct=f.down_payment_pct, ufmip_pct=f.ufmip_pct, annual_mip_pct=f.annual_mip_pct,
        annual_mip_pct_5pct_down=f.annual_mip_pct_5pct_down, closing_cost_pct=f.closing_cost_pct,
        reserve_months=f.reserve_months_3_4_units, vacancy_repair_reserve_pct=f.vacancy_repair_reserve_pct,
        self_sufficiency_factor=f.self_sufficiency_factor, market_rent_factor=f.market_rent_factor,
        term_months=f.loan_term_months, target_cashflow=f.target_cashflow, with_dpa=with_dpa,
    )
