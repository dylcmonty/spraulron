"""Settings loaded from config.toml plus .env (API keys)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

import tomli
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent


class AppCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    user_agent: str = "spraulron/0.1"
    cache_dir: str = ".cache"
    data_dir: str = "data"
    cache_prune_days: int = 7
    auction_lookahead_days: int = 75


class CountiesCfg(BaseModel):
    names: list[str]
    fips: dict[str, str]


class MapCfg(BaseModel):
    center: list[float] = [41.25, -81.55]
    zoom: int = 9
    rentcast_radius_miles: float = 40


class MarketCfg(BaseModel):
    sold_past_days: int = 180
    rent_rel_sd_fallback: float = 0.15


class FhaCfg(BaseModel):
    rate_fallback: float = 0.07375
    rate_dpa_fallback: float = 0.08
    down_payment_pct: float = 0.035
    ufmip_pct: float = 0.0175
    annual_mip_pct: float = 0.0055
    annual_mip_pct_5pct_down: float = 0.005
    closing_cost_pct: float = 0.03
    reserve_months_3_4_units: int = 3
    vacancy_repair_reserve_pct: float = 0.20
    self_sufficiency_factor: float = 0.75
    market_rent_factor: float = 1.0
    target_cashflow: float = 300
    loan_term_months: int = 360
    loan_limits: dict[str, int] = Field(default_factory=dict)
    insurance_monthly: dict[str, float] = Field(default_factory=dict)
    utilities_monthly: dict[str, float] = Field(default_factory=dict)
    tax_rate: dict[str, float] = Field(default_factory=lambda: {"default": 0.023})
    tax_rate_city: dict[str, float] = Field(default_factory=dict)


class Settings(BaseModel):
    app: AppCfg = AppCfg()
    counties: CountiesCfg
    map: MapCfg = MapCfg()
    market: MarketCfg = MarketCfg()
    ttl_hours: dict[str, float] = Field(default_factory=dict)
    fha: FhaCfg = FhaCfg()
    rentcast_api_key: Optional[str] = None
    hud_api_token: Optional[str] = None
    root: Path = ROOT

    def ttl_seconds(self, key: str, default_hours: float = 6) -> int:
        return int(self.ttl_hours.get(key, default_hours) * 3600)

    @property
    def cache_path(self) -> Path:
        return self.root / self.app.cache_dir

    @property
    def data_path(self) -> Path:
        return self.root / self.app.data_dir

    def tax_rate_for(self, county: Optional[str], city: Optional[str]) -> float:
        if city:
            for name, rate in self.fha.tax_rate_city.items():
                if name.lower() == city.strip().lower():
                    return rate
        if county and county in self.fha.tax_rate:
            return self.fha.tax_rate[county]
        return self.fha.tax_rate.get("default", 0.023)

    def _by_units(self, table: dict[str, float], units: Optional[int]) -> float:
        if not table:
            return 0.0
        key = str(min(max(units or 1, 1), 4))
        if key in table:
            return table[key]
        return table[max(table, key=lambda k: int(k))]

    def insurance_for(self, units: Optional[int]) -> float:
        return self._by_units(self.fha.insurance_monthly, units)

    def utilities_for(self, units: Optional[int]) -> float:
        return self._by_units(self.fha.utilities_monthly, units)


def load_settings(path: Optional[Path] = None) -> Settings:
    load_dotenv(ROOT / ".env")
    cfg_path = path or ROOT / "config.toml"
    with open(cfg_path, "rb") as fh:
        raw = tomli.load(fh)
    s = Settings(**raw)
    s.rentcast_api_key = os.environ.get("RENTCAST_API_KEY") or None
    s.hud_api_token = os.environ.get("HUD_API_TOKEN") or None
    s.cache_path.mkdir(parents=True, exist_ok=True)
    s.data_path.mkdir(parents=True, exist_ok=True)
    return s


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
