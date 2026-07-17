"""
Загрузка данных о облигациях из MOEX ISS API.
Опционально: Cbonds API для кредитных рейтингов.
"""
from __future__ import annotations

import math
import statistics
import sys
import warnings
from datetime import date, datetime, timedelta
from typing import Any, Optional

import requests

from cache import MOEXCache
from config import Config

MOEX_BASE = "https://iss.moex.com/iss"
MOEX_BONDS = f"{MOEX_BASE}/engines/stock/markets/bonds"
MOEX_HISTORY = f"{MOEX_BASE}/history/engines/stock/markets/bonds"
REQUEST_TIMEOUT = 15


class MOEXFetcher:
    """Загрузчик данных с Московской биржи через ISS API."""

    def __init__(self, config: Config, cache: MOEXCache):
        self.config = config
        self.cache = cache
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "moex-bonds-analyzer/1.0"})

    def _fetch_json(self, url: str) -> Optional[Any]:
        """GET-запрос с кэшированием. Возвращает None при ошибке."""
        cached = self.cache.get(url)
        if cached is not None:
            return cached

        try:
            resp = self._session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            self.cache.set(url, data)
            return data
        except requests.RequestException as e:
            warnings.warn(f"MOEX API error: {e} for {url}")
            return None
        except ValueError as e:
            warnings.warn(f"MOEX JSON parse error: {e} for {url}")
            return None

    @staticmethod
    def _desc_to_dict(data: list) -> dict[str, Any]:
        """MOEX description data — список [name, title, value, type, ...]."""
        return {row[0]: row[2] for row in data}

    @staticmethod
    def _mdata_to_dict(columns: list[str], data: list) -> dict[str, Any]:
        """MOEX marketdata columns + data -> dict."""
        if not data:
            return {}
        return dict(zip(columns, data[0]))

    def search_by_isin(self, isin: str) -> list[dict[str, str]]:
        """Поиск бумаги по ISIN на MOEX."""
        url = f"{MOEX_BASE}/securities.json?q={isin}&iss.meta=off"
        data = self._fetch_json(url)
        if not data or "securities" not in data:
            return []
        cols = data["securities"].get("columns", [])
        rows = data["securities"].get("data", [])
        results = []
        for row in rows:
            entry = dict(zip(cols, row))
            # Фильтруем только облигации
            if entry.get("group") in ("stock_bonds", "bonds"):
                results.append(entry)
        return results

    def _get_secid(self, isin: str) -> str:
        """Получить SECID (торговый код) по ISIN."""
        results = self.search_by_isin(isin)
        if not results:
            raise ValueError(f"ISIN {isin} не найден на MOEX")
        # Предпочитаем TQCB board
        secid = results[0].get("secid", "")
        if not secid:
            raise ValueError(f"Не удалось определить SECID для {isin}")
        return secid

    def get_primary_board(self, secid: str) -> str | None:
        """Определить основную торговую доску для облигации.

        Приоритет: TQOB (ОФЗ) → TQCB (корп. облигации) → любая TQ*.

        Parameters
        ----------
        secid :
            Торговый код облигации.

        Returns
        -------
        str | None
            Код доски (TQOB/TQCB/...) или None, если не найдена.
        """
        url = f"{MOEX_BASE}/securities/{secid}/boards.json?iss.meta=off"
        data = self._fetch_json(url)
        if not data:
            return None
        boards_raw = data.get("boards", {}).get("data", [])
        cols = data.get("boards", {}).get("columns", [])
        if not cols or not boards_raw:
            return None

        boards = [dict(zip(cols, row)) for row in boards_raw]

        # Приоритет: TQOB → TQCB → любая TQ*
        for b in boards:
            bid = b.get("boardid", "")
            if bid == "TQOB":
                return "TQOB"
        for b in boards:
            bid = b.get("boardid", "")
            if bid == "TQCB":
                return "TQCB"
        for b in boards:
            bid = b.get("boardid", "")
            if bid.startswith("TQ"):
                return bid
        return None

    def get_description(self, secid: str) -> dict[str, Any]:
        """Параметры выпуска облигации."""
        url = f"{MOEX_BASE}/securities/{secid}.json?iss.meta=off"
        data = self._fetch_json(url)
        if not data:
            return {}
        desc_raw = data.get("description", {}).get("data", [])
        return self._desc_to_dict(desc_raw)

    def get_market_data(self, secid: str, board: str | None = None) -> dict[str, Any]:
        """Рыночные котировки и доходности.

        Parameters
        ----------
        secid :
            Торговый код облигации.
        board :
            Доска (TQCB, TQOB, …). Если указана — запрашивает данные
            только по этой доске, что даёт корректные YIELD / LAST / ...
        """
        if board:
            url = f"{MOEX_BONDS}/boards/{board}/securities/{secid}.json?iss.meta=off"
        else:
            url = f"{MOEX_BONDS}/securities/{secid}.json?iss.meta=off"
        data = self._fetch_json(url)
        if not data:
            return {}

        result = {}

        # Securities (board info + ACCRUEDINT, COUPONVALUE, etc.)
        sec_raw = data.get("securities", {})
        if sec_raw.get("data"):
            result["securities"] = self._mdata_to_dict(
                sec_raw.get("columns", []), sec_raw.get("data", [])
            )

        # Marketdata (prices, NKD, volume)
        md_raw = data.get("marketdata", {})
        if md_raw.get("data"):
            md = self._mdata_to_dict(md_raw.get("columns", []), md_raw.get("data", []))
            result["marketdata"] = md

        # Marketdata yields
        my_raw = data.get("marketdata_yields", {})
        if my_raw.get("data"):
            my = self._mdata_to_dict(my_raw.get("columns", []), my_raw.get("data", []))
            result["marketdata_yields"] = my

        return result

    def get_history(
        self, secid: str, days: int = 30
    ) -> list[dict[str, Any]]:
        """История торгов за N дней."""
        from_date = (date.today() - timedelta(days=days)).isoformat()
        url = (
            f"{MOEX_HISTORY}/securities/{secid}.json?"
            f"iss.meta=off&limit={min(days, 100)}&from={from_date}"
        )
        data = self._fetch_json(url)
        if not data:
            return []
        hist_raw = data.get("history", {})
        cols = hist_raw.get("columns", [])
        rows = hist_raw.get("data", [])
        return [dict(zip(cols, row)) for row in rows]

    def get_cbr_key_rate(self) -> float:
        """
        Ключевая ставка ЦБ РФ — парсит с https://cbr.ru/hd_base/KeyRate/.
        Всегда берёт свежее значение с сайта ЦБ.
        """
        import re

        try:
            resp = self._session.get(
                "https://cbr.ru/hd_base/KeyRate/",
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            resp.raise_for_status()
            html = resp.text

            pattern = re.compile(
                r"<td>\s*(\d{2}\.\d{2}\.\d{4})\s*</td>\s*<td>\s*(\d{1,2},\d{2})\s*</td>"
            )
            matches = pattern.findall(html)
            if matches:
                rate_str = matches[0][1].replace(",", ".")
                rate = float(rate_str) / 100.0
                if 0.05 <= rate <= 0.30:
                    return rate

            raise RuntimeError("Не удалось распарсить ключевую ставку с cbr.ru")
        except Exception as e:
            raise RuntimeError(
                f"Ошибка получения ключевой ставки с cbr.ru: {e}. "
                f"Проверьте подключение к интернету."
            )

    def get_top_bonds(self, limit: int = 25) -> list[dict[str, Any]]:
        """
        Получить список торгуемых облигаций с доски TQCB.
        """
        return self.get_board_bonds("TQCB", limit=limit)

    def get_board_bonds(
        self, board: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """
        Получить список облигаций с указанной доски.

        Parameters
        ----------
        board :
            Код доски (TQCB — корпоративные, TQOB — ОФЗ, …).
        limit :
            Максимальное количество бумаг.

        Returns
        -------
        list[dict[str, Any]]
            Каждый элемент — словарь с полями из marketdata
            (SECID, YIELD, LAST, ACCRUEDINT, PREVPRICE, …).
        """
        url = (
            f"{MOEX_BONDS}/boards/{board}/securities.json?"
            f"iss.meta=off&iss.only=marketdata&limit={limit}"
        )
        data = self._fetch_json(url)
        if not data:
            return []

        md_raw = data.get("marketdata", {})
        cols = md_raw.get("columns", [])
        rows = md_raw.get("data", [])
        bonds = []
        for row in rows:
            entry = dict(zip(cols, row))
            secid = entry.get("SECID", "")
            if secid:
                bonds.append(entry)
        return bonds

    def get_board_bonds_full(
        self, board: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Получить облигации доски с marketdata + securities (ISIN, name).

        Один запрос MOEX вместо N отдельных ``get_description``.

        Returns
        -------
        list[dict[str, Any]]
            Объединённые словари: marketdata + securities для каждой бумаги.
        """
        url = (
            f"{MOEX_BONDS}/boards/{board}/securities.json?"
            f"iss.meta=off&iss.only=marketdata,securities&limit={limit}"
        )
        data = self._fetch_json(url)
        if not data:
            return []

        # marketdata
        md_raw = data.get("marketdata", {})
        md_cols = md_raw.get("columns", [])
        md_rows = md_raw.get("data", [])
        md_by_secid: dict[str, dict] = {}
        for row in md_rows:
            entry = dict(zip(md_cols, row))
            secid = entry.get("SECID", "")
            if secid:
                md_by_secid[secid] = entry

        # securities
        sec_raw = data.get("securities", {})
        sec_cols = sec_raw.get("columns", [])
        sec_rows = sec_raw.get("data", [])

        result: list[dict[str, Any]] = []
        for row in sec_rows:
            sec_entry = dict(zip(sec_cols, row))
            secid = sec_entry.get("SECID", "")
            if not secid:
                continue
            md = md_by_secid.get(secid, {})
            merged = {**sec_entry, **md}  # marketdata перезаписывает при конфликте
            result.append(merged)
        return result

    def get_bond_data(
        self, isin: str, board: str | None = None
    ) -> dict[str, Any]:
        """
        Агрегировать все данные по ISIN в единый словарь.

        Parameters
        ----------
        isin :
            ISIN облигации.
        board :
            Код доски (TQOB, TQCB, …). Если None — определяется
            автоматически через ``get_primary_board()``.
        """
        secid = self._get_secid(isin)

        # Определяем доску, если не указана
        if board is None:
            board = self.get_primary_board(secid)

        desc = self.get_description(secid)
        mdata_raw = self.get_market_data(secid, board=board)

        mdata = mdata_raw.get("marketdata", {}) if mdata_raw else {}
        secdata = mdata_raw.get("securities", {}) if mdata_raw else {}
        myields = mdata_raw.get("marketdata_yields", {}) if mdata_raw else {}

        # --- Извлекаем параметры ---
        face_value = float(desc.get("FACEVALUE", desc.get("INITIALFACEVALUE", 1000)))
        current_price = 100.0  # fallback
        nkd = 0.0
        coupon_value = 0.0
        coupon_percent = 0.0
        coupon_frequency = 4  # типично для РФ
        yield_moex = None
        duration_days = 0
        volume_today = 0
        num_trades = 0
        coupon_period_days = 91  # fallback
        last_price = None
        offer_date = None
        days_to_offer = None
        # Уровень листинга: по умолчанию 3 (консервативный — низколиквидные)
        list_level = 3
        z_spread = None
        effective_yield_moex = None

        # Из marketdata
        if mdata:
            # Сначала LAST, затем PREVPRICE, затем LASTADMITTER (цена последней сделки)
            for price_field in ("LAST", "PREVPRICE", "LASTADMITTER"):
                try:
                    val = mdata.get(price_field)
                    if val is not None and val != "null":
                        current_price = float(val)
                        break
                except (ValueError, TypeError):
                    pass

            # ACCRUEDINT может быть в секции securities (не marketdata)
            for src_name, src in [("mdata", mdata), ("secdata", secdata)]:
                try:
                    raw = src.get("ACCRUEDINT", "0")
                    val = float(raw) if raw not in (None, "null", "") else 0.0
                    if val != 0.0:
                        nkd = val
                        break
                except (ValueError, TypeError):
                    pass

            for src in (mdata, secdata):
                try:
                    cv = src.get("COUPONVALUE", None)
                    if cv not in (None, "null", ""):
                        coupon_value = float(cv)
                        break
                except (ValueError, TypeError):
                    pass

            for src in (mdata, secdata):
                try:
                    cp = src.get("COUPONPERCENT", None)
                    if cp not in (None, "null", ""):
                        coupon_percent = float(cp)
                        break
                except (ValueError, TypeError):
                    pass

            try:
                vol = mdata.get("VALTODAY", "0")
                volume_today = float(vol) if vol not in (None, "null", "") else 0.0
            except (ValueError, TypeError):
                pass

            try:
                nt = mdata.get("NUMTRADES", "0")
                num_trades = int(nt) if nt not in (None, "null", "") else 0
            except (ValueError, TypeError):
                pass

            try:
                dur = mdata.get("DURATION", "0")
                duration_days = int(dur) if dur not in (None, "null", "") else 0
            except (ValueError, TypeError):
                pass

            try:
                yld = mdata.get("YIELD", None)
                if yld is not None and yld != "null" and yld != "":
                    yield_moex = float(yld)
            except (ValueError, TypeError):
                pass

            try:
                ofd = mdata.get("OFFERDATE", None)
                if ofd is not None and ofd not in ("null", "", "0000-00-00"):
                    offer_date = ofd
                    try:
                        ofd_date = datetime.strptime(ofd, "%Y-%m-%d").date()
                        days_to_offer = (ofd_date - date.today()).days
                    except ValueError:
                        pass
            except (ValueError, TypeError):
                pass

            try:
                lvl = secdata.get("LISTLEVEL", "2")
                list_level = int(lvl) if lvl not in (None, "null", "") else 2
            except (ValueError, TypeError):
                pass

        # Из описания (приоритет для купонных параметров, если в marketdata пусто)
        if desc:
            try:
                cf = desc.get("COUPONFREQUENCY", coupon_frequency)
                coupon_frequency = int(cf) if cf not in (None, "null", "") else coupon_frequency
            except (ValueError, TypeError):
                pass

            try:
                cpd = desc.get("COUPONPERIOD", coupon_period_days)
                coupon_period_days = int(cpd) if cpd not in (None, "null", "") else coupon_period_days
            except (ValueError, TypeError):
                pass

            if coupon_percent == 0.0:
                try:
                    cp = desc.get("COUPONPERCENT", "0")
                    coupon_percent = float(cp) if cp not in (None, "null", "") else 0.0
                except (ValueError, TypeError):
                    pass

            if coupon_value == 0.0:
                try:
                    cv = desc.get("COUPONVALUE", "0")
                    coupon_value = float(cv) if cv not in (None, "null", "") else 0.0
                except (ValueError, TypeError):
                    pass

        # Из marketdata_yields
        if myields:
            try:
                zsp = myields.get("ZSPREADBP", None)
                if zsp is not None and zsp != "null" and zsp != "":
                    z_spread = float(zsp) / 100.0  # в %
            except (ValueError, TypeError):
                pass

            try:
                ey = myields.get("EFFECTIVEYIELD", None)
                if ey is not None and ey != "null" and ey != "":
                    effective_yield_moex = float(ey)
            except (ValueError, TypeError):
                pass

        # Дни до погашения
        days_to_maturity = 0
        maturity_date = None
        if desc:
            matdate = desc.get("MATDATE", "")
            if matdate:
                maturity_date = matdate
                try:
                    md = datetime.strptime(matdate, "%Y-%m-%d").date()
                    days_to_maturity = (md - date.today()).days
                    if days_to_maturity < 0:
                        days_to_maturity = 0
                except ValueError:
                    pass

            # Из description если DAYSTOREDEMPTION есть
            if days_to_maturity == 0:
                try:
                    dtr = desc.get("DAYSTOREDEMPTION", "0")
                    days_to_maturity = int(dtr) if dtr not in (None, "null", "") else 0
                except (ValueError, TypeError):
                    pass

        if days_to_maturity <= 0 and duration_days > 0:
            days_to_maturity = duration_days

        # Цена в рублях
        price_value = face_value * current_price / 100.0

        # История для среднего объёма
        avg_daily_volume = 0.0
        try:
            history = self.get_history(secid, days=30)
            if history:
                volumes = []
                for h in history:
                    try:
                        val = float(h.get("VALUE", 0))
                        if val > 0:
                            volumes.append(val)
                    except (ValueError, TypeError):
                        pass
                if volumes:
                    avg_daily_volume = statistics.mean(volumes)
        except Exception:
            pass

        # Эмитент
        emitent_id = desc.get("EMITTER_ID", desc.get("EMITENT_ID", ""))

        return {
            "isin": isin,
            "secid": secid,
            "shortname": desc.get("SHORTNAME", ""),
            "fullname": desc.get("NAME", ""),
            "latname": desc.get("LATNAME", ""),
            "regnumber": desc.get("REGNUMBER", ""),
            "face_value": face_value,
            "current_price": round(current_price, 2),
            "price_value": round(price_value, 2),
            "nkd": round(nkd, 2),
            "coupon_value": coupon_value,
            "coupon_percent": coupon_percent,
            "coupon_frequency": coupon_frequency,
            "coupon_period_days": coupon_period_days,
            "days_to_maturity": days_to_maturity,
            "maturity_date": maturity_date or "",
            "offer_date": offer_date or None,
            "days_to_offer": days_to_offer,
            "list_level": list_level,
            "yield_moex": yield_moex,
            "duration_moex_days": duration_days,
            "volume_today": volume_today,
            "num_trades_today": num_trades,
            "avg_daily_volume": avg_daily_volume,
            "emitent_id": emitent_id,
            "z_spread": z_spread,
            "effective_yield_moex": effective_yield_moex,
            "isin_listed": True,
        }

    def close(self) -> None:
        self._session.close()


class CbondsFetcher:
    """Опциональный загрузчик данных Cbonds."""

    BASE_URL = "http://cbonds.ru/api/v2"

    def __init__(self, login: str = "Test", password: str = "Test"):
        self.auth = (login, password)
        self._session = requests.Session()
        self._session.auth = self.auth
        self._session.headers.update({"User-Agent": "moex-bonds-analyzer/1.0"})

    def get_issuer_rating(self, isin: str) -> Optional[dict[str, Any]]:
        """Получить кредитный рейтинг эмитента по ISIN."""
        try:
            url = f"{self.BASE_URL}/ratings/search?isin={isin}"
            resp = self._session.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                return data
            return None
        except requests.RequestException as e:
            warnings.warn(f"Cbonds API error: {e}")
            return None

    def close(self) -> None:
        self._session.close()
