"""
Логика взвешенных рекомендаций на основе метрик облигаций.
Включает DTS (Duration × Spread) и стресс-сценарии.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from config import Config


class Recommender:
    """
    Вычисляет взвешенный балл и выдаёт сигнал (покупать/держать/продавать).
    Никаких подгоночных коэффициентов — только объективные метрики.
    """

    # Маппинг уровня листинга -> кредитный балл (0..1)
    LIST_LEVEL_SCORES = {
        1: 1.0,   # высший уровень (обычно крупные эмитенты)
        2: 0.7,   # стандарт
        3: 0.4,   # низколиквидные
    }

    # Налоговый статус: баллы
    TAX_SCORES = {
        "ordinary": 0.5,   # обычная облигация — налог 13%
        "substituted": 0.7, # замещающие — налог 13% но в валюте
        "sovereign": 1.0,   # суверенные (ОФЗ) — налог 13% только с купона >1М
    }

    def __init__(self, config: Config):
        self.cfg = config

    # ------------------------------------------------------------------
    #  Helper: единый метод для получения YTM
    # ------------------------------------------------------------------

    def _get_ytm(self, bond_data: dict[str, Any], calc_results: dict[str, Any]) -> float:
        """
        Получить корректную YTM для облигации.

        Для коротких бумаг (<180 дн.) используем консервативный simple_ytm,
        т.к. MOEX YTM для них аномально завышен из-за аннуализации копеечной прибыли.

        Для обычных бумаг — рыночный YTM от MOEX, с fallback на simple_ytm.
        Безопасно обрабатывает None (баг #1).
        """
        days = bond_data.get("days_to_maturity", 0)
        if 0 < days < 180:
            return calc_results.get("simple_ytm", {}).get("annual_return_pct", 0) or 0.0

        ytm = bond_data.get("yield_moex") or 0
        if ytm <= 0:
            ytm = calc_results.get("simple_ytm", {}).get("annual_return_pct", 0) or 0.0
        return ytm

    def rate(
        self,
        bond_data: dict[str, Any],
        calc_results: dict[str, Any],
        scenario_results: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Основной метод: оценка облигации.

        bond_data — сырые данные из MOEXFetcher.get_bond_data()
        calc_results — результаты из BondCalculator.calculate_all()
        scenario_results — опционально: результаты ScenarioAnalyzer.analyze_bond()

        Возвращает словарь с:
        - score: общий балл (0..1)
        - signal: "ПОКУПАТЬ" | "ДЕРЖАТЬ" | "ПРОДАВАТЬ"
        - details: разбивка по факторам
        - justification: текст обоснования
        - risks: список рисков
        """
        details = {}
        weights = self.cfg.scoring

        # 1. Доходность к погашению (25%)
        yield_score = self._score_yield(bond_data, calc_results)
        details["yield"] = {"weight": weights.yield_weight, "score": yield_score}

        # 2. Кредитное качество (20%)
        credit_score = self._score_credit(bond_data)
        details["credit"] = {"weight": weights.credit_weight, "score": credit_score}

        # 3. Дюрация (15%)
        duration_score = self._score_duration(bond_data, calc_results)
        details["duration"] = {"weight": weights.duration_weight, "score": duration_score}

        # 4. DTS — Duration Times Spread (15%)
        dts_score = self._score_dts(bond_data, calc_results)
        details["dts"] = {"weight": weights.dts_weight, "score": dts_score}

        # 5. Ликвидность (10%)
        liquidity_score = self._score_liquidity(bond_data)
        details["liquidity"] = {"weight": weights.liquidity_weight, "score": liquidity_score}

        # 6. Стресс-сценарии (10%)
        scenario_score = self._score_scenario(bond_data, calc_results, scenario_results)
        details["scenario"] = {"weight": weights.scenario_weight, "score": scenario_score}

        # 7. Налоговый статус (5%)
        tax_score = self._score_tax_status(bond_data)
        details["tax"] = {"weight": weights.tax_weight, "score": tax_score}

        # Взвешенная сумма
        total_score = sum(
            d["weight"] * d["score"]
            for d in details.values()
        )

        # Сигнал
        signal = self._determine_signal(total_score, bond_data, calc_results)

        # Обоснование
        justification = self._build_justification(
            signal, total_score, details, bond_data, calc_results,
        )

        # Риски
        risks = self._identify_risks(bond_data, calc_results)

        return {
            "score": round(total_score, 4),
            "signal": signal,
            "details": details,
            "justification": justification,
            "risks": risks,
        }

    # ------------------------------------------------------------------
    #  Factor scorers
    # ------------------------------------------------------------------

    def _score_yield(self, bond_data: dict[str, Any], calc_results: dict[str, Any]) -> float:
        """
        Оценка доходности (0..1).
        Чем выше доходность относительно безрисковой ставки — тем лучше.
        """
        ytm = self._get_ytm(bond_data, calc_results)

        rf_rate = self.cfg.risk_free_rate
        if rf_rate is None:
            rf_rate = 0.145  # Ставка ЦБ по умолчанию если не загружена (14.5%)
        rf_pct = rf_rate * 100.0

        spread = max(0, ytm - rf_pct)
        # spread 0% -> 0, spread >= 10% -> 1.0
        score = min(1.0, spread / 10.0)
        return round(score, 4)

    def _score_credit(self, bond_data: dict[str, Any]) -> float:
        """
        Оценка кредитного качества (0..1).
        Уровень листинга как прокси кредитного качества.
        Если данные отсутствуют — консервативно используем уровень 3.
        """
        list_level = bond_data.get("list_level", 3)
        if list_level is None:
            list_level = 3
        return self.LIST_LEVEL_SCORES.get(list_level, 0.3)

    def _score_duration(self, bond_data: dict[str, Any], calc_results: dict[str, Any]) -> float:
        """
        Оценка дюрации (0..1).
        Идеальный срок — 20-50% от инвестиционного горизонта.
        Слишком короткие (<20% горизонта): риск реинвестирования, копеечная прибыль.
        Слишком длинные (>200% горизонта): высокий процентный риск.
        """
        horizon = self.cfg.investment_horizon_days
        duration_days = bond_data.get("duration_moex_days", 0)
        days_to_mat = bond_data.get("days_to_maturity", 0)
        if duration_days > 0:
            effective_days = duration_days
        elif days_to_mat > 0:
            # Если дюрация MOEX недоступна — аппроксимируем.
            # Для купонных облигаций Macaulay duration < времени до погашения.
            # Коэффициент: 0.7 для quarterly, 0.8 для semi-annual/annual.
            coupon_freq = bond_data.get("coupon_frequency", 4)
            proxy_factor = 0.7 if coupon_freq >= 4 else 0.8
            effective_days = int(days_to_mat * proxy_factor)
        else:
            effective_days = 0

        if effective_days <= 0 or horizon <= 0:
            return 0.5

        ratio = effective_days / horizon
        # Слишком короткие — нет времени заработать, высокий реинвестиционный риск
        if ratio < 0.2:
            return 0.1
        elif ratio <= 0.5:
            return 1.0
        elif ratio <= 1.0:
            return 0.8
        elif ratio <= 1.5:
            return 0.5
        elif ratio <= 2.0:
            return 0.3
        else:
            return 0.1

    def _score_dts(self, bond_data: dict[str, Any], calc_results: dict[str, Any]) -> float:
        """
        Оценка DTS (Duration × Spread) — риск-скорректированная мера (0..1).

        DTS = ModifiedDuration × Z-spread (в %).

        Чем ниже DTS при той же доходности — тем эффективнее использование
        спред-риска.  Высокий DTS = много риска за каждый bp спреда.
        """
        mod_dur = calc_results.get("modified_duration_years", 0)
        # Безопасное извлечение: dict.get() с существующим ключом=None вернёт None
        z_spread = bond_data.get("z_spread") or 0.0  # уже в процентах

        if mod_dur <= 0 or z_spread <= 0:
            return 0.5  # нет данных — нейтрально

        dts = mod_dur * z_spread

        # DTS: 0 → 1.0 (отлично), 10+ → 0.0 (плохо)
        # Формула: score = 1 / (1 + DTS / 2)
        score = 1.0 / (1.0 + dts / 2.0)
        return round(score, 4)

    def _score_liquidity(self, bond_data: dict[str, Any]) -> float:
        """
        Оценка ликвидности (0..1).
        Среднедневной объём торгов как прокси ликвидности.
        """
        avg_vol = bond_data.get("avg_daily_volume", 0)
        if avg_vol <= 0:
            return 0.1
        score = min(1.0, math.log10(avg_vol / 1_000_000 + 1) * 0.5)
        return round(score, 4)

    def _score_scenario(
        self,
        bond_data: dict[str, Any],
        calc_results: dict[str, Any],
        scenario_results: dict[str, Any] | None,
    ) -> float:
        """
        Оценка устойчивости к стресс-сценариям (0..1).

        Если scenario_results не передан — используем прокси на основе
        дюрации: чем выше дюрация, тем ниже балл (больше просадка при
        росте ставок).
        """
        if scenario_results:
            # Используем наихудший сценарий (кроме базового)
            worst_change = 0.0
            for key, res in scenario_results.items():
                if key == "base":
                    continue
                pct = res.get("price_change_pct", 0)
                if pct < worst_change:
                    worst_change = pct

            # worst_change: 0% → 1.0, -20% → 0.0
            score = 1.0 - min(1.0, abs(worst_change) / 20.0)
            return round(score, 4)

        # Прокси: чем ниже модифицированная дюрация — тем устойчивее
        mod_dur = calc_results.get("modified_duration_years", 0)
        if mod_dur <= 0:
            return 0.5
        # Дюрация 0 → 1.0, дюрация 10+ → 0.0
        score = 1.0 / (1.0 + mod_dur / 3.0)
        return round(score, 4)

    def _score_tax_status(self, bond_data: dict[str, Any]) -> float:
        """Оценка налогового статуса (0..1)."""
        isin = bond_data.get("isin", "")
        name = bond_data.get("shortname", "").lower()
        fullname = bond_data.get("fullname", "").lower()
        combined_name = name + " " + fullname
        # ОФЗ — суверенные, налог 13% только с купона > 1 млн
        if isin.startswith("RU") and ("офз" in combined_name or "о фз" in combined_name):
            return self.TAX_SCORES["sovereign"]
        # Замещающие облигации — валютная привязка, особый налоговый режим
        if "замещ" in combined_name or "substituted" in combined_name:
            return self.TAX_SCORES["substituted"]
        return self.TAX_SCORES["ordinary"]

    # ------------------------------------------------------------------
    #  Signal & justification
    # ------------------------------------------------------------------

    def _determine_signal(
        self,
        score: float,
        bond_data: dict[str, Any],
        calc_results: dict[str, Any],
    ) -> str:
        """Определить итоговый сигнал."""
        thresholds = self.cfg.thresholds
        ytm = self._get_ytm(bond_data, calc_results)
        rf_rate = self.cfg.risk_free_rate or 0.145
        rf_pct = rf_rate * 100.0

        if score >= thresholds.buy_score:
            if ytm > rf_pct + thresholds.yield_spread_over_rf * 100:
                return "ПОКУПАТЬ"
            return "ДЕРЖАТЬ"
        elif score >= 0.55:
            if ytm > rf_pct + thresholds.yield_spread_over_rf * 50:
                return "ДОКУПАТЬ"
            return "ДЕРЖАТЬ"
        elif score >= thresholds.sell_score:
            return "ДЕРЖАТЬ"
        else:
            return "ПРОДАВАТЬ"

    def _build_justification(
        self,
        signal: str,
        score: float,
        details: dict[str, Any],
        bond_data: dict[str, Any],
        calc_results: dict[str, Any],
    ) -> str:
        """Сформировать текстовое обоснование."""
        lines = [f"Агрегированный балл: {score:.2f} / 1.00"]
        lines.append("")

        for factor, d in details.items():
            lines.append(f"  • {factor}: {d['score']:.2f} (вес {d['weight']*100:.0f}%)")

        lines.append("")
        ytm_val = self._get_ytm(bond_data, calc_results)
        rf_rate = self.cfg.risk_free_rate or 0.145

        if signal == "ПОКУПАТЬ":
            lines.append(
                f"Доходность ({ytm_val:.1f}%) значительно превышает безрисковую "
                f"ставку ({rf_rate*100:.1f}%) — облигация привлекательна."
            )
        elif signal == "ДОКУПАТЬ":
            lines.append(
                f"Доходность ({ytm_val:.1f}%) выше безрисковой ({rf_rate*100:.1f}%) "
                f"— хороший кандидат для докупки при ухудшении цены."
            )
        elif signal == "ДЕРЖАТЬ":
            lines.append(
                f"Доходность ({ytm_val:.1f}%) около рыночного уровня "
                f"({rf_rate*100:.1f}%) — нет явных катализаторов."
            )
        else:
            lines.append(
                f"Низкая доходность ({ytm_val:.1f}%) и/или слабые метрики "
                f"— рекомендуется заменить на альтернативу."
            )

        # Добавляем DTS, если доступен
        dts_detail = details.get("dts", {})
        if dts_detail.get("score", 0) > 0:
            lines.append(
                f"DTS (Duration×Spread): {dts_detail['score']:.2f} — "
                f"{'хорошая компенсация за риск' if dts_detail['score'] > 0.5 else 'высокая цена за спред-риск'}."
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    #  Risk identification
    # ------------------------------------------------------------------

    def _identify_risks(
        self, bond_data: dict[str, Any], calc_results: dict[str, Any]
    ) -> list[str]:
        """Выявить риски."""
        risks = []

        avg_vol = bond_data.get("avg_daily_volume", 0)
        if avg_vol < 1_000_000:
            risks.append("Низкая ликвидность — могут быть проблемы с продажей")

        days_to_mat = bond_data.get("days_to_maturity", 0)
        if 0 < days_to_mat < 90:
            risks.append("Короткий срок до погашения — реинвестиционный риск, прибыль минимальна")

        days_to_offer = bond_data.get("days_to_offer")
        if days_to_offer is not None and days_to_offer < 30:
            risks.append(f"Близкая оферта ({days_to_offer} дн.) — возможен досрочный отзыв")

        nkd = bond_data.get("nkd", 0)
        price = bond_data.get("price_value", 0)
        if price > 0 and nkd / price > 0.05:
            risks.append("Высокий НКД относительно цены")

        if bond_data.get("list_level", 3) == 3:
            risks.append("Низкий уровень листинга — повышенный кредитный риск")

        # DTS-риск
        mod_dur = calc_results.get("modified_duration_years", 0)
        z_spread = bond_data.get("z_spread") or 0.0
        if mod_dur > 5 and z_spread > 3.0:
            risks.append(
                f"Высокий DTS ({mod_dur:.1f}×{z_spread:.1f}) — "
                f"чувствительность к расширению спредов"
            )

        return risks
