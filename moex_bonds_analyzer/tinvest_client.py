"""
T-Bank Invest API client (REST).

Docs: https://developer.tbank.ru/invest/api
Proto: https://opensource.tbank.ru/invest/invest-contracts
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

import requests

from config import TInvestConfig


BASE_URL = "https://invest-public-api.tbank.ru/rest"
SANDBOX_URL = "https://sandbox-invest-public-api.tbank.ru/rest"


def _money_to_float(mv: Optional[dict]) -> float:
    """Convert MoneyValue {units, nano} to float."""
    if not mv:
        return 0.0
    units = int(mv.get("units", "0"))
    nano = int(mv.get("nano", 0))
    return units + nano / 1e9


def _quotation_to_float(q: Optional[dict]) -> float:
    """Convert Quotation {units, nano} to float."""
    if not q:
        return 0.0
    units = int(q.get("units", "0"))
    nano = int(q.get("nano", 0))
    return units + nano / 1e9


class TInvestClient:
    """REST клиент для T-Invest API."""

    def __init__(self, config: TInvestConfig, verify_ssl: bool = True) -> None:
        self.config = config
        self.verify_ssl = verify_ssl
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {config.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self._base = SANDBOX_URL if config.sandbox else BASE_URL
        if not verify_ssl:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # ── Accounts ──────────────────────────────────────────────────────────

    def _req(self, url: str, json_data: dict) -> requests.Response:
        """Send POST with verify_ssl."""
        return self._session.post(url, json=json_data, verify=self.verify_ssl)

    def get_accounts(self) -> list[dict]:
        """Get all user accounts."""
        resp = self._req(
            f"{self._base}/tinkoff.public.invest.api.contract.v1.UsersService/GetAccounts",
            {},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("accounts", [])

    def get_first_broker_account_id(self) -> Optional[str]:
        """Get first open broker account ID."""
        for acc in self.get_accounts():
            if acc.get("status") == "ACCOUNT_STATUS_OPEN":
                acc_type = acc.get("type", "")
                if acc_type in ("ACCOUNT_TYPE_TINKOFF", "ACCOUNT_TYPE_TINKOFF_IIS"):
                    return acc.get("id")
        # fallback: first open account
        for acc in self.get_accounts():
            if acc.get("status") == "ACCOUNT_STATUS_OPEN":
                return acc.get("id")
        return None

    def get_broker_accounts(self) -> list[dict]:
        """Get all open T-Invest broker accounts."""
        result = []
        for acc in self.get_accounts():
            if acc.get("status") == "ACCOUNT_STATUS_OPEN":
                acc_type = acc.get("type", "")
                if acc_type in ("ACCOUNT_TYPE_TINKOFF", "ACCOUNT_TYPE_TINKOFF_IIS"):
                    result.append({
                        "id": acc.get("id", ""),
                        "name": acc.get("name", "Без названия"),
                        "type": acc_type,
                    })
        return result

    def get_portfolio(self, account_id: str) -> dict:
        """Get portfolio for account."""
        resp = self._req(
            f"{self._base}/tinkoff.public.invest.api.contract.v1.OperationsService/GetPortfolio",
            {"accountId": account_id, "currency": "RUB"},
        )
        resp.raise_for_status()
        return resp.json()

    def get_positions(self, account_id: str) -> dict:
        """Get positions for account."""
        resp = self._req(
            f"{self._base}/tinkoff.public.invest.api.contract.v1.OperationsService/GetPositions",
            {"accountId": account_id},
        )
        resp.raise_for_status()
        return resp.json()

    # ── Instruments ───────────────────────────────────────────────────────

    def get_instrument_by_figi(self, figi: str) -> Optional[dict]:
        """Get instrument info by FIGI (returns ISIN for bonds)."""
        resp = self._req(
            f"{self._base}/tinkoff.public.invest.api.contract.v1.InstrumentsService/GetInstrumentBy",
            {"idType": "INSTRUMENT_ID_TYPE_FIGI", "id": figi},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data.get("instrument")

    def find_instrument(self, query: str) -> list[dict]:
        """Search instruments by ticker/name."""
        resp = self._req(
            f"{self._base}/tinkoff.public.invest.api.contract.v1.InstrumentsService/FindInstrument",
            {"query": query},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("instruments", [])

    # ── Helper: portfolio → enriched list ────────────────────────────────

    def get_bond_positions(self, account_id: Optional[str] = None) -> list[dict]:
        """Get bond positions from portfolio as enriched list.

        Returns list of dicts with keys:
            figi, isin, ticker, name, quantity, average_price, current_price,
            current_nkd, expected_yield, daily_yield, instrument_type
        """
        if not account_id:
            account_id = self.get_first_broker_account_id()
        if not account_id:
            return []

        portfolio = self.get_portfolio(account_id)
        positions = portfolio.get("positions", [])

        bond_positions = []
        for pos in positions:
            if pos.get("instrumentType") != "bond":
                continue

            figi = pos.get("figi", "")
            if not figi:
                continue

            # Resolve FIGI → ISIN
            instr = self.get_instrument_by_figi(figi)
            isin = (instr or {}).get("isin", "")
            name = (instr or {}).get("name", "")
            ticker = (instr or {}).get("ticker", "")

            # If fallback: use ticker + class_code from position
            if not name:
                name = pos.get("ticker", "")
            if not isin:
                isin = figi  # fallback

            qty = _quotation_to_float(pos.get("quantity"))
            avg_price = _money_to_float(pos.get("averagePositionPrice"))
            current_price = _money_to_float(pos.get("currentPrice"))
            nkd = _money_to_float(pos.get("currentNkd"))
            expected_yield = _quotation_to_float(pos.get("expectedYield"))
            daily_yield = _money_to_float(pos.get("dailyYield"))

            bond_positions.append({
                "figi": figi,
                "isin": isin,
                "ticker": ticker,
                "name": name,
                "quantity": qty,
                "average_price": avg_price,
                "current_price": current_price,
                "current_nkd": nkd,
                "expected_yield_pct": expected_yield,
                "daily_yield_rub": daily_yield,
                "instrument_type": "bond",
            })

        return bond_positions

    def get_portfolio_summary(self) -> Optional[dict]:
        """Get portfolio summary (total value, bonds value, daily yield)."""
        account_id = self.get_first_broker_account_id()
        if not account_id:
            return None
        portfolio = self.get_portfolio(account_id)
        return {
            "total_amount_bonds": _money_to_float(portfolio.get("totalAmountBonds")),
            "total_amount_portfolio": _money_to_float(portfolio.get("totalAmountPortfolio")),
            "daily_yield": _money_to_float(portfolio.get("dailyYield")),
            "daily_yield_relative": _quotation_to_float(portfolio.get("dailyYieldRelative")),
            "expected_yield_pct": _quotation_to_float(portfolio.get("expectedYield")),
            "account_id": portfolio.get("accountId", ""),
        }

    def get_all_bonds(self) -> list[dict]:
        """Get all active bonds from T-Invest API (entire market).

        Returns list of dicts with:
            figi, ticker, isin, name, classCode, nominal, couponQuantityPerYear,
            maturityDate, aciValue, riskLevel, sector, buyAvailableFlag, sellAvailableFlag
        """
        resp = self._req(
            f"{self._base}/tinkoff.public.invest.api.contract.v1.InstrumentsService/Bonds",
            {"instrumentStatus": "INSTRUMENT_STATUS_BASE"},
        )
        resp.raise_for_status()
        data = resp.json()
        instruments = data.get("instruments", [])

        result = []
        for inst in instruments:
            nominal_val = _money_to_float(inst.get("nominal"))
            aci_val = _money_to_float(inst.get("aciValue"))
            maturity = inst.get("maturityDate", "")
            coupon_freq = inst.get("couponQuantityPerYear", 0)

            result.append({
                "figi": inst.get("figi", ""),
                "ticker": inst.get("ticker", ""),
                "isin": inst.get("isin", ""),
                "name": inst.get("name", ""),
                "classCode": inst.get("classCode", ""),
                "nominal": nominal_val,
                "couponQuantityPerYear": coupon_freq,
                "maturityDate": maturity,
                "aciValue": aci_val,
                "riskLevel": inst.get("riskLevel", ""),
                "sector": inst.get("sector", ""),
                "buyAvailableFlag": inst.get("buyAvailableFlag", False),
                "sellAvailableFlag": inst.get("sellAvailableFlag", False),
                "tradingStatus": inst.get("tradingStatus", ""),
                "countryOfRisk": inst.get("countryOfRisk", ""),
            })

        return result
