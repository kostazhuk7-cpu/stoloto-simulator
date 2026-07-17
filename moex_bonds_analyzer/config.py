"""
Загрузка конфигурации из config.yaml + переменные окружения.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class ScoringWeights:
    yield_weight: float = 0.25
    credit_weight: float = 0.20
    duration_weight: float = 0.15
    dts_weight: float = 0.15
    liquidity_weight: float = 0.10
    scenario_weight: float = 0.10
    tax_weight: float = 0.05


@dataclass
class Thresholds:
    buy_score: float = 0.65
    sell_score: float = 0.40
    yield_spread_over_rf: float = 0.02


@dataclass
class CbondsConfig:
    login: str = ""
    password: str = ""


@dataclass
class TInvestConfig:
    token: str = ""
    sandbox: bool = False
    account_id: str = ""


@dataclass
class Config:
    tax_rate: float = 0.13
    broker_commission: float = 0.0004
    risk_free_rate: Optional[float] = None
    investment_horizon_days: int = 730
    scoring: ScoringWeights = field(default_factory=ScoringWeights)
    thresholds: Thresholds = field(default_factory=Thresholds)
    cbonds: CbondsConfig = field(default_factory=CbondsConfig)
    tinvest: TInvestConfig = field(default_factory=TInvestConfig)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Config":
        """Загрузить config.yaml, наложить переменные окружения."""
        if path is None:
            path = Path(__file__).parent / "config.yaml"

        cfg = cls()

        if path.exists():
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            cfg.tax_rate = raw.get("tax_rate", cfg.tax_rate)
            cfg.broker_commission = raw.get("broker_commission", cfg.broker_commission)
            cfg.risk_free_rate = raw.get("risk_free_rate")
            cfg.investment_horizon_days = raw.get("investment_horizon_days", cfg.investment_horizon_days)

            sc = raw.get("scoring", {})
            cfg.scoring.yield_weight = sc.get("yield_weight", cfg.scoring.yield_weight)
            cfg.scoring.credit_weight = sc.get("credit_weight", cfg.scoring.credit_weight)
            cfg.scoring.duration_weight = sc.get("duration_weight", cfg.scoring.duration_weight)
            cfg.scoring.liquidity_weight = sc.get("liquidity_weight", cfg.scoring.liquidity_weight)
            cfg.scoring.dts_weight = sc.get("dts_weight", cfg.scoring.dts_weight)
            cfg.scoring.scenario_weight = sc.get("scenario_weight", cfg.scoring.scenario_weight)
            cfg.scoring.tax_weight = sc.get("tax_weight", cfg.scoring.tax_weight)

            th = raw.get("thresholds", {})
            cfg.thresholds.buy_score = th.get("buy_score", cfg.thresholds.buy_score)
            cfg.thresholds.sell_score = th.get("sell_score", cfg.thresholds.sell_score)
            cfg.thresholds.yield_spread_over_rf = th.get("yield_spread_over_rf", cfg.thresholds.yield_spread_over_rf)

            cb = raw.get("cbonds", {})
            cfg.cbonds.login = cb.get("login", "")
            cfg.cbonds.password = cb.get("password", "")

            ti = raw.get("tinvest", {})
            cfg.tinvest.token = ti.get("token", "")
            cfg.tinvest.sandbox = ti.get("sandbox", False)
            cfg.tinvest.account_id = ti.get("account_id", "")

        # Переменные окружения переопределяют YAML
        env_map = {
            "MOEX_TAX_RATE": ("tax_rate", float),
            "MOEX_COMMISSION": ("broker_commission", float),
            "MOEX_RISK_FREE_RATE": ("risk_free_rate", float),
            "MOEX_INVEST_HORIZON": ("investment_horizon_days", int),
            "MOEX_CBONDS_LOGIN": ("cbonds", "login", str),
            "MOEX_CBONDS_PASSWORD": ("cbonds", "password", str),
            "MOEX_TINVEST_TOKEN": ("tinvest", "token", str),
            "MOEX_TINVEST_ACCOUNT_ID": ("tinvest", "account_id", str),
        }
        for env_key, mapping in env_map.items():
            val = os.environ.get(env_key)
            if val is not None:
                if isinstance(mapping, tuple) and len(mapping) == 3:
                    # nested field: (nested_attr, field_name, cast)
                    _, field_name, cast = mapping
                    setattr(cfg.cbonds, field_name, cast(val))
                elif isinstance(mapping, tuple):
                    attr, cast = mapping
                    setattr(cfg, attr, cast(val))
                else:
                    setattr(cfg, mapping, val)

        return cfg
