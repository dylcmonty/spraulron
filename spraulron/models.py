"""Pydantic models shared by sources, finance and the web layer."""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class Unit(BaseModel):
    bedrooms: Optional[int] = None
    bathrooms: Optional[float] = None
    rent: Optional[float] = None
    rent_source: str = "fmr"  # fmr | estimate | override | listing
    owner_occupied: bool = False


class Listing(BaseModel):
    id: str
    source: str  # homeharvest | rentcast
    source_label: str = ""
    url: Optional[str] = None
    address: str
    city: Optional[str] = None
    state: str = "OH"
    zip: Optional[str] = None
    county: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    price: Optional[float] = None
    units: Optional[int] = None
    units_source: str = "unknown"  # source | text | override | assumed | unknown
    beds: Optional[int] = None
    baths: Optional[float] = None
    sqft: Optional[int] = None
    lot_sqft: Optional[int] = None
    year_built: Optional[int] = None
    days_on_market: Optional[int] = None
    listed_date: Optional[str] = None
    status: Optional[str] = None
    property_type: Optional[str] = None
    description: Optional[str] = None
    photo: Optional[str] = None
    photos: list[str] = Field(default_factory=list)
    mls: Optional[str] = None
    mls_id: Optional[str] = None
    tax_annual: Optional[float] = None
    assessed_value: Optional[float] = None
    hoa_monthly: Optional[float] = None
    agent: Optional[str] = None
    office: Optional[str] = None
    flags: list[str] = Field(default_factory=list)
    links: dict[str, str] = Field(default_factory=dict)


class AuctionItem(BaseModel):
    id: str
    source: str  # realauction_summit | realauction_cuyahoga | stark_sheriff | cuyahoga_forfeited | cuyahoga_landbank
    source_label: str = ""
    county: str
    sale_type: str = "Unknown"  # Delinquent Tax | Mortgage Foreclosure | Forfeited Land | Land Bank | Tax Certificate | Unknown
    sale_date: Optional[date] = None
    sale_time: Optional[str] = None
    status: Optional[str] = None
    address: str
    city: Optional[str] = None
    zip: Optional[str] = None
    parcel: Optional[str] = None
    case_number: Optional[str] = None
    plaintiff_defendant: Optional[str] = None
    appraised: Optional[float] = None
    appraised_label: str = "Appraised"
    opening_bid: Optional[float] = None
    deposit: Optional[float] = None
    high_bid: Optional[float] = None
    is_vacant_lot: bool = False
    property_class: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    photo: Optional[str] = None
    url: Optional[str] = None
    notes: Optional[str] = None
    links: dict[str, str] = Field(default_factory=dict)


class FhaAssumptions(BaseModel):
    rate: float
    down_payment_pct: float = 0.035
    ufmip_pct: float = 0.0175
    annual_mip_pct: float = 0.0055
    annual_mip_pct_5pct_down: float = 0.005
    tax_rate: Optional[float] = None  # annual fraction of price; resolved per county/city when None
    insurance_monthly: Optional[float] = None  # resolved by unit count when None
    utilities_monthly: Optional[float] = None
    closing_cost_pct: float = 0.03
    reserve_months: int = 3
    vacancy_repair_reserve_pct: float = 0.20
    self_sufficiency_factor: float = 0.75
    market_rent_factor: float = 1.0
    term_months: int = 360
    target_cashflow: float = 300
    with_dpa: bool = False


class FhaQuote(BaseModel):
    price: float
    units: Optional[int]
    rate: float
    down_payment_pct: float
    down_payment: float
    base_loan: float
    ufmip: float
    loan_amount: float
    principal_interest: float
    mip_monthly: float
    tax_monthly: float
    tax_rate_used: float
    insurance_monthly: float
    piti: float
    utilities_monthly: float
    closing_costs: float
    cash_to_close: float
    reserves_required: float
    total_cash_needed: float
    tenant_rent: Optional[float] = None
    all_units_rent: Optional[float] = None
    reserve_allowance: Optional[float] = None
    cash_flow: Optional[float] = None
    break_even_price: Optional[float] = None
    target_cashflow: Optional[float] = None
    target_price: Optional[float] = None
    self_sufficiency_rent: Optional[float] = None
    self_sufficiency_pass: Optional[bool] = None
    within_loan_limit: Optional[bool] = None
    flags: list[str] = Field(default_factory=list)
