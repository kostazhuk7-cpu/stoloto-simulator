"""
Финансовые расчёты для облигаций.
Все формулы консервативны — без реинвестирования купонов.
"""
from __future__ import annotations

import math
from typing import Any, Optional


class BondCalculator:
    """Статические методы для расчёта метрик облигаций."""

    @staticmethod
    def current_yield(
        annual_coupon_rub: float, price_value: float
    ) -> float:
        """Текущая купонная доходность = Годовой купон / Цена."""
        if price_value <= 0:
            return 0.0
        return (annual_coupon_rub / price_value) * 100.0

    @staticmethod
    def total_commission(
        price_value: float, commission_rate: float
    ) -> float:
        return price_value * commission_rate

    @staticmethod
    def simple_yield_to_maturity(
        *,
        face_value: float,
        price_value: float,
        nkd: float,
        coupon_value: float,
        coupon_frequency: int,
        days_to_maturity: int,
        remaining_coupons: Optional[int] = None,
        tax_rate: float = 0.13,
        commission_rate: float = 0.0004,
    ) -> dict[str, Any]:
        """
        Простая доходность к погашению (без реинвестирования купонов).
        Возвращает dict со всеми промежуточными значениями для прозрачности.
        """
        if remaining_coupons is None:
            # Количество оставшихся купонов (округляем ВНИЗ — неполный купон не получим)
            coupon_period_days = 365 / coupon_frequency if coupon_frequency else 365
            remaining_coupons = int(days_to_maturity / coupon_period_days)
            if remaining_coupons < 0:
                remaining_coupons = 0

        comm_purchase = BondCalculator.total_commission(price_value, commission_rate)
        total_expense = price_value + nkd + comm_purchase

        total_coupons_income = coupon_value * remaining_coupons
        # Комиссия при получении купонов (обычно 0, но учитываем)
        total_coupon_commission = coupon_value * commission_rate * remaining_coupons
        capital_gain = face_value - price_value

        gross_income = capital_gain + total_coupons_income - total_coupon_commission
        tax = max(0.0, gross_income * tax_rate) if gross_income > 0 else 0.0
        net_income = gross_income - tax

        if total_expense <= 0:
            return {"error": "Цена не может быть нулевой"}

        total_return_pct = net_income / total_expense

        if days_to_maturity > 0:
            annual_return_pct = total_return_pct * (365.0 / days_to_maturity) * 100.0
        else:
            annual_return_pct = 0.0

        return {
            "type": "simple_ytm",
            "price_value": round(price_value, 2),
            "nkd": round(nkd, 2),
            "commission_purchase": round(comm_purchase, 2),
            "total_expense": round(total_expense, 2),
            "face_value": round(face_value, 2),
            "capital_gain": round(capital_gain, 2),
            "total_coupons_income": round(total_coupons_income, 2),
            "remaining_coupons": remaining_coupons,
            "gross_income": round(gross_income, 2),
            "tax": round(tax, 2),
            "net_income": round(net_income, 2),
            "total_return_pct": round(total_return_pct * 100, 4),
            "days_to_maturity": days_to_maturity,
            "annual_return_pct": round(annual_return_pct, 4),
        }

    @staticmethod
    def effective_ytm(
        *,
        face_value: float,
        price_value: float,
        nkd: float,
        coupon_value: float,
        coupon_frequency: int,
        days_to_maturity: int,
        offer_price: Optional[float] = None,
        days_to_offer: Optional[int] = None,
        tax_rate: float = 0.13,
        commission_rate: float = 0.0004,
        max_iter: int = 200,
        tolerance: float = 1e-8,
    ) -> dict[str, Any]:
        """
        Эффективная доходность к погашению (YTM) численным методом.
        Учитывает налог на купонный доход и комиссию при покупке.

        Используется метод Ньютона для решения уравнения:
        P + НКД + комиссия = Σ (CF_i * (1-налог) / (1+r)^(t_i)) + N / (1+r)^(T)

        Без допущения о реинвестировании купонов.
        """
        comm_purchase = BondCalculator.total_commission(price_value, commission_rate)
        total_cost = price_value + nkd + comm_purchase

        # Аномальный НКД — невозможно рассчитать корректную доходность
        if nkd > face_value * 0.5:
            annual_coupon_rub = coupon_value * coupon_frequency
            fallback_ytm = (annual_coupon_rub / total_cost * 100) if total_cost > 0 else 0.0
            return {
                "type": "effective_ytm",
                "total_cost": round(total_cost, 2),
                "ytm_pct": round(fallback_ytm, 4),
                "macaulay_duration_years": 0.0,
                "modified_duration_years": 0.0,
                "convexity": 0.0,
                "modified_convexity": 0.0,
                "num_cash_flows": 0,
                "date_type": "maturity",
                "method": "fallback",
                "converged": False,
                "error": f"Anomalous NKD ({nkd:.0f} > {face_value * 0.5:.0f}) — used coupon yield fallback",
            }

        # Строим денежные потоки
        cash_flows: list[tuple[float, float]] = []  # (time_years, cf_after_tax)

        target_date = "maturity"
        target_value = face_value
        target_days = days_to_maturity

        if days_to_offer is not None and offer_price is not None:
            target_date = "offer"
            target_value = offer_price
            target_days = days_to_offer

        if target_days <= 0:
            return {
                "type": "effective_ytm",
                "error": "Нулевой срок до погашения/оферты",
                "ytm_pct": 0.0,
            }

        # Купонный период в годах
        period_years = 1.0 / coupon_frequency if coupon_frequency else 1.0
        num_periods = int(target_days / (period_years * 365))
        if num_periods < 1:
            num_periods = 1

        # Добавляем купонные платежи (с учётом налога)
        net_coupon = coupon_value * (1 - tax_rate)
        for i in range(1, num_periods + 1):
            t = period_years * i
            cash_flows.append((t, net_coupon))

        # Добавляем номинал (или цену оферты) с вычетом налога на прирост капитала
        T = target_days / 365.0
        if T < 0.001:
            T = 1.0 / 365.0
        capital_gain = target_value - price_value
        target_after_tax = target_value - max(0.0, capital_gain * tax_rate)
        cash_flows.append((T, target_after_tax))

        def npv(rate: float) -> float:
            r = rate
            pv = 0.0
            for t, cf in cash_flows:
                pv += cf / ((1.0 + r) ** t)
            return pv - total_cost

        def npv_deriv(rate: float) -> float:
            r = rate
            deriv = 0.0
            for t, cf in cash_flows:
                deriv += -t * cf / ((1.0 + r) ** (t + 1.0))
            return deriv

        # Поиск методом Ньютона
        # Начальное приближение — доходность к погашению (номинал + купоны) / стоимость
        annual_coupon_rub = coupon_value * coupon_frequency
        total_coupon_income = annual_coupon_rub * (target_days / 365.0)
        capital_gain = target_value - price_value
        total_return = total_coupon_income + capital_gain
        guess = max(0.001, min(2.0, total_return / total_cost))

        ytm = guess
        converged = False
        for _ in range(max_iter):
            f = npv(ytm)
            if abs(f) < tolerance:
                converged = True
                break
            fp = npv_deriv(ytm)
            if abs(fp) < 1e-12:
                ytm *= 0.5
                continue
            ytm_new = ytm - f / fp
            if ytm_new <= 0:
                ytm /= 2.0
            else:
                ytm = ytm_new

        ytm_pct = max(0.0, ytm * 100.0)

        # Дюрация Маколея (при найденной YTM)
        duration = BondCalculator._calc_duration(cash_flows, total_cost, ytm)
        mod_duration = duration / (1.0 + ytm) if ytm >= 0 else duration

        # Выпуклость (при найденной YTM)
        convexity = BondCalculator._calc_convexity(cash_flows, total_cost, ytm)
        # Полувещественная выпуклость (видоизменённая) = convexity / (1 + y)²
        mod_convexity = convexity / ((1.0 + ytm) ** 2) if ytm >= 0 else convexity

        return {
            "type": f"effective_ytm_to_{target_date}",
            "total_cost": round(total_cost, 2),
            "ytm_pct": round(ytm_pct, 4),
            "macaulay_duration_years": round(duration, 4),
            "modified_duration_years": round(mod_duration, 4),
            "convexity": round(convexity, 4),
            "modified_convexity": round(mod_convexity, 4),
            "num_cash_flows": len(cash_flows),
            "date_type": target_date,
            "method": "newton",
            "converged": converged,
        }

    @staticmethod
    def _calc_duration(
        cash_flows: list[tuple[float, float]],
        total_cost: float,
        ytm: float,
    ) -> float:
        """Дюрация Маколея: Σ(t * PV(CF)) / Σ(PV(CF))."""
        if total_cost <= 0 or ytm < 0:
            return 0.0
        weighted_sum = 0.0
        pv_sum = 0.0
        for t, cf in cash_flows:
            pv = cf / ((1.0 + ytm) ** t)
            pv_sum += pv
            weighted_sum += t * pv
        if pv_sum <= 0:
            return 0.0
        return weighted_sum / pv_sum

    @staticmethod
    def _calc_convexity(
        cash_flows: list[tuple[float, float]],
        total_cost: float,
        ytm: float,
    ) -> float:
        """
        Выпуклость (annualised convexity): Σ(t * (t+1) * CF / (1+r)^(t+2)) / P.

        Использует те же денежные потоки, что и duration.
        Возвращает выпуклость в годах².
        """
        if total_cost <= 0 or ytm < 0:
            return 0.0
        one_plus_r = 1.0 + ytm
        sum_conv = 0.0
        for t, cf in cash_flows:
            # t = время в годах, cf = денежный поток (чистый, после налога)
            pv = cf / (one_plus_r ** t)
            sum_conv += t * (t + 1.0) * pv
        # Конвексити = сумма / (P * (1+r)²)
        return sum_conv / (total_cost * (one_plus_r ** 2))

    @staticmethod
    def calculate_all(
        bond_data: dict[str, Any],
        tax_rate: float = 0.13,
        commission_rate: float = 0.0004,
    ) -> dict[str, Any]:
        """
        Агрегированный расчёт всех метрик.
        bond_data — словарь из data_fetcher.get_bond_data().
        """
        face_value = bond_data.get("face_value", 1000.0)
        current_price = bond_data.get("current_price", 100.0)
        price_value = bond_data.get("price_value", face_value * current_price / 100.0)
        if price_value == 0:
            price_value = face_value * current_price / 100.0
        nkd = bond_data.get("nkd", 0.0)
        coupon_value = bond_data.get("coupon_value", 0.0)
        coupon_percent = bond_data.get("coupon_percent", 0.0)
        coupon_frequency = bond_data.get("coupon_frequency", 4)
        days_to_maturity = bond_data.get("days_to_maturity", 0)
        offer_date = bond_data.get("offer_date")
        days_to_offer = bond_data.get("days_to_offer")

        annual_coupon_rub = coupon_value * coupon_frequency

        # --- Current yield ---
        cur_yield = BondCalculator.current_yield(annual_coupon_rub, price_value)

        # --- Simple YTM ---
        simple_ytm = BondCalculator.simple_yield_to_maturity(
            face_value=face_value,
            price_value=price_value,
            nkd=nkd,
            coupon_value=coupon_value,
            coupon_frequency=coupon_frequency,
            days_to_maturity=days_to_maturity,
            tax_rate=tax_rate,
            commission_rate=commission_rate,
        )

        # --- Effective YTM ---
        eff_ytm = BondCalculator.effective_ytm(
            face_value=face_value,
            price_value=price_value,
            nkd=nkd,
            coupon_value=coupon_value,
            coupon_frequency=coupon_frequency,
            days_to_maturity=days_to_maturity,
            tax_rate=tax_rate,
            commission_rate=commission_rate,
        )

        # --- YTM to offer (if exists) ---
        eff_ytm_offer = None
        if days_to_offer and days_to_offer > 0:
            eff_ytm_offer = BondCalculator.effective_ytm(
                face_value=face_value,
                price_value=price_value,
                nkd=nkd,
                coupon_value=coupon_value,
                coupon_frequency=coupon_frequency,
                days_to_maturity=days_to_offer,
                offer_price=face_value,
                days_to_offer=days_to_offer,
                tax_rate=tax_rate,
                commission_rate=commission_rate,
            )

        # --- Duration from MOEX or calculated ---
        moex_duration_days = bond_data.get("duration_moex_days", 0)
        moex_ytm = bond_data.get("yield_moex", eff_ytm.get("ytm_pct", 0) if eff_ytm else 0)

        result = {
            "current_price_pct": current_price,
            "price_value_rub": round(price_value, 2),
            "nkd_rub": round(nkd, 2),
            "annual_coupon_rub": round(annual_coupon_rub, 2),
            "coupon_percent": coupon_percent,
            "coupon_frequency": coupon_frequency,
            "current_yield_pct": round(cur_yield, 4),
            "simple_ytm": simple_ytm,
            "effective_ytm": eff_ytm,
            "effective_ytm_offer": eff_ytm_offer,
            "moex_duration_days": moex_duration_days,
            "moex_ytm_pct": moex_ytm,
            "macaulay_duration_years": eff_ytm.get("macaulay_duration_years", 0),
            "modified_duration_years": eff_ytm.get("modified_duration_years", 0),
            "convexity": eff_ytm.get("convexity", 0),
            "modified_convexity": eff_ytm.get("modified_convexity", 0),
        }
        return result
