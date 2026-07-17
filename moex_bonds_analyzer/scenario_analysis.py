"""
Scenario/stress testing for bond portfolios.
Estimates price changes under various interest rate and spread scenarios
using duration + convexity approximation.

Формула:
    dP/P ≈ -D_mod * dy + 0.5 * C * (dy)^2
    где dy = (изменение_ставки + изменение_спреда) в десятичном виде
"""
from __future__ import annotations

import math
from typing import Any


class ScenarioAnalyzer:
    """Analyze bond portfolio under various scenarios.

    All methods are static — no state, pure computation.
    Uses modified duration and estimated convexity from bond_calculator outputs.
    """

    # Default scenarios for Russian market
    DEFAULT_SCENARIOS: dict[str, dict[str, Any]] = {
        "base": {
            "name": "Базовый",
            "rate_change_bps": 0,
            "spread_change_bps": 0,
        },
        "rate_down_100": {
            "name": "Снижение КС на 1%",
            "rate_change_bps": -100,
            "spread_change_bps": 0,
        },
        "rate_down_200": {
            "name": "Снижение КС на 2%",
            "rate_change_bps": -200,
            "spread_change_bps": 0,
        },
        "rate_up_100": {
            "name": "Повышение КС на 1%",
            "rate_change_bps": 100,
            "spread_change_bps": 0,
        },
        "rate_up_200": {
            "name": "Повышение КС на 2%",
            "rate_change_bps": 200,
            "spread_change_bps": 0,
        },
        "spread_widen": {
            "name": "Рост кредитных спредов",
            "rate_change_bps": 0,
            "spread_change_bps": 100,
        },
        "spread_compress": {
            "name": "Сжатие спредов",
            "rate_change_bps": 0,
            "spread_change_bps": -100,
        },
        "steepening": {
            "name": "Понижение кривой",
            "rate_change_bps": -100,
            "spread_change_bps": 0,
            "curve_shift": "steepen",
        },
        "recession": {
            "name": "Рецессия",
            "rate_change_bps": -200,
            "spread_change_bps": 150,
        },
    }

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_modified_duration(calc_results: dict[str, Any]) -> float:
        """Extract modified duration in years from calculator results.

        Falls back to Macaulay duration / (1 + YTM) if ``modified_duration_years``
        is missing or zero.
        """
        mod_dur = calc_results.get("modified_duration_years", 0)
        if mod_dur and mod_dur > 0:
            return mod_dur

        mac_dur = calc_results.get("macaulay_duration_years", 0)
        if mac_dur and mac_dur > 0:
            eff: dict[str, Any] = calc_results.get("effective_ytm", {}) or {}
            ytm = eff.get("ytm_pct", 0) / 100.0
            return mac_dur / (1.0 + ytm) if ytm >= 0 else mac_dur

        return 0.0

    @staticmethod
    def _estimate_convexity(calc_results: dict[str, Any]) -> float:
        """Estimate convexity from Macaulay duration and YTM.

        Approximation for a bullet bond:
            C ≈ (D² + D) / (1 + y)²

        where *D* is Macaulay duration in years and *y* is YTM (decimal).
        Returns 0 when insufficient data is available.
        """
        mac_dur = calc_results.get("macaulay_duration_years", 0)
        if not mac_dur or mac_dur <= 0:
            return 0.0

        eff: dict[str, Any] = calc_results.get("effective_ytm", {}) or {}
        ytm = eff.get("ytm_pct", 0) / 100.0
        if ytm < 0:
            ytm = 0.0

        return (mac_dur ** 2 + mac_dur) / ((1.0 + ytm) ** 2)

    @staticmethod
    def _compute_price_change_pct(
        mod_duration: float,
        convexity: float,
        dy: float,
    ) -> float:
        """Price change percent via duration + convexity.

        ``dy`` is the total yield change in decimal form (e.g. 0.01 for 1 %).

        Returns the estimated price change as a percentage
        (e.g. -2.5 for -2.5 %).
        """
        return (-mod_duration * dy + 0.5 * convexity * (dy ** 2)) * 100.0

    @staticmethod
    def _probability_estimate(scenario: dict[str, Any]) -> str:
        """Classify scenario likelihood based on the magnitude of yield shift."""
        rate_change = abs(scenario.get("rate_change_bps", 0))
        spread_change = abs(scenario.get("spread_change_bps", 0))
        total_bps = rate_change + spread_change

        if total_bps == 0:
            return "очень вероятно"
        if total_bps <= 100:
            return "вероятно"
        if total_bps <= 200:
            return "возможно"
        return "стресс-сценарий"

    @staticmethod
    def _scenario_display_name(key: str) -> str:
        """Return human-readable scenario name in Russian."""
        info = ScenarioAnalyzer.DEFAULT_SCENARIOS.get(key)
        return info["name"] if info else key

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    @staticmethod
    def analyze_bond(
        bond_data: dict[str, Any],
        calc_results: dict[str, Any],
        scenarios: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Analyse a single bond under each scenario.

        Uses the duration + convexity approximation:
            dP/P ≈ -D_mod · dy + 0.5 · C · (dy)²

        where ``dy = (rate_change_bps + spread_change_bps) / 10 000``.

        Parameters
        ----------
        bond_data :
            Raw bond data from ``data_fetcher.get_bond_data()``.
        calc_results :
            Calculated metrics from ``bond_calculator.calculate_all()``.
        scenarios :
            Custom scenario dict, or ``None`` to use :attr:`DEFAULT_SCENARIOS`.

        Returns
        -------
        dict
            ``{scenario_key: {price_change_pct, pnl_rub, new_price,
            probability_estimate}}``
        """
        if scenarios is None:
            scenarios = ScenarioAnalyzer.DEFAULT_SCENARIOS

        mod_dur = ScenarioAnalyzer._get_modified_duration(calc_results)
        convexity = ScenarioAnalyzer._estimate_convexity(calc_results)

        current_price_value = bond_data.get(
            "price_value",
            calc_results.get("price_value_rub", 0),
        )
        current_price_pct = bond_data.get(
            "current_price",
            calc_results.get("current_price_pct", 100),
        )

        results: dict[str, dict[str, Any]] = {}
        for key, scenario in scenarios.items():
            rate_change_bps = scenario.get("rate_change_bps", 0)
            spread_change_bps = scenario.get("spread_change_bps", 0)
            dy = (rate_change_bps + spread_change_bps) / 10000.0

            price_change_pct = ScenarioAnalyzer._compute_price_change_pct(
                mod_dur, convexity, dy,
            )
            pnl_rub = price_change_pct / 100.0 * current_price_value
            new_price = current_price_pct * (1.0 + price_change_pct / 100.0)

            results[key] = {
                "price_change_pct": round(price_change_pct, 4),
                "pnl_rub": round(pnl_rub, 2),
                "new_price": round(new_price, 2),
                "probability_estimate": ScenarioAnalyzer._probability_estimate(
                    scenario,
                ),
            }

        return results

    @staticmethod
    def analyze_portfolio(
        portfolio: list[dict[str, Any]],
        scenarios: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Aggregate scenario analysis across a portfolio of bonds.

        Parameters
        ----------
        portfolio :
            Each entry::

                {
                    "bond_data": dict,      # from data_fetcher
                    "calc_results": dict,   # from bond_calculator
                    "quantity": int | float,  # number of bonds held
                }
        scenarios :
            Custom scenario dict, or ``None`` for :attr:`DEFAULT_SCENARIOS`.

        Returns
        -------
        dict
            ``{scenario_key: {total_pnl, portfolio_value_change_pct,
            worst_bond, best_bond, bond_count}}``
        """
        if scenarios is None:
            scenarios = ScenarioAnalyzer.DEFAULT_SCENARIOS

        if not portfolio:
            return {}

        # -- Initialise accumulators --
        aggregated: dict[str, dict[str, Any]] = {}
        for key in scenarios:
            aggregated[key] = {
                "total_pnl": 0.0,
                "bond_results": [],
                "portfolio_value": 0.0,
            }

        # -- Process each bond --
        for entry in portfolio:
            bond_data: dict[str, Any] = entry.get("bond_data", {})
            calc_results: dict[str, Any] = entry.get("calc_results", {})
            quantity: float = entry.get("quantity", 1)

            bond_name = bond_data.get(
                "shortname",
                bond_data.get("isin", "Unknown"),
            )
            price_value = bond_data.get(
                "price_value",
                calc_results.get("price_value_rub", 0),
            )
            bond_results = ScenarioAnalyzer.analyze_bond(
                bond_data, calc_results, scenarios,
            )
            position_value = price_value * quantity
            face_value = bond_data.get("face_value", 1000)

            for key in scenarios:
                br = bond_results[key]
                pnl = br["pnl_rub"] * quantity

                # New position value = current + pnl (most reliable)
                new_position_value = br["new_price"] / 100.0 * face_value * quantity

                agg = aggregated[key]
                agg["total_pnl"] += pnl
                agg["portfolio_value"] += position_value
                agg["bond_results"].append({
                    "name": bond_name,
                    "pnl_rub": round(pnl, 2),
                    "price_change_pct": br["price_change_pct"],
                    "new_position_value": round(new_position_value, 2),
                })

        # -- Summarise per scenario --
        results: dict[str, dict[str, Any]] = {}
        for key, agg in aggregated.items():
            bond_pnls = agg["bond_results"]
            if not bond_pnls:
                results[key] = {
                    "total_pnl": 0.0,
                    "portfolio_value_change_pct": 0.0,
                    "worst_bond": {"name": "", "pnl_rub": 0.0},
                    "best_bond": {"name": "", "pnl_rub": 0.0},
                    "bond_count": 0,
                }
                continue

            worst = min(bond_pnls, key=lambda x: x["pnl_rub"])
            best = max(bond_pnls, key=lambda x: x["pnl_rub"])

            port_val = agg["portfolio_value"]
            port_change_pct = (
                (agg["total_pnl"] / port_val * 100.0) if port_val > 0 else 0.0
            )

            results[key] = {
                "total_pnl": round(agg["total_pnl"], 2),
                "portfolio_value_change_pct": round(port_change_pct, 4),
                "worst_bond": {
                    "name": worst["name"],
                    "pnl_rub": round(worst["pnl_rub"], 2),
                    "price_change_pct": round(worst["price_change_pct"], 4),
                },
                "best_bond": {
                    "name": best["name"],
                    "pnl_rub": round(best["pnl_rub"], 2),
                    "price_change_pct": round(best["price_change_pct"], 4),
                },
                "bond_count": len(portfolio),
            }

        return results

    @staticmethod
    def probability_weighted_return(
        scenario_results: dict[str, dict[str, Any]],
        probabilities: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """Calculate probability-weighted expected return across scenarios.

        Parameters
        ----------
        scenario_results :
            Output from :meth:`analyze_bond` or :meth:`analyze_portfolio`.
        probabilities :
            ``{scenario_key: weight}``.  Auto-derived when ``None``.

            Default distribution:
                * base … 50 %
                * rate_down_* … 15 %  (split evenly among variants)
                * rate_up_* … 15 %    (split evenly among variants)
                * spread_* … 10 %     (split evenly among variants)
                * recession … 10 %
                * unclassified … remaining probability split evenly

        Returns
        -------
        dict
            Keys: ``expected_return_pct``, ``variance``, ``std_dev_pct``,
            ``sharpe_ratio_estimate``, ``num_scenarios``.
        """
        if probabilities is None:
            probabilities = ScenarioAnalyzer._default_probabilities(
                scenario_results,
            )

        # Normalise to 1.0
        total_prob = sum(probabilities.values())
        if total_prob <= 0:
            return {
                "expected_return_pct": 0.0,
                "variance": 0.0,
                "std_dev_pct": 0.0,
                "sharpe_ratio_estimate": 0.0,
                "num_scenarios": 0,
            }

        norm_probs = {k: v / total_prob for k, v in probabilities.items()}

        # Determine return field — portfolio or single-bond results
        sample = next(iter(scenario_results.values()), {})
        ret_field = (
            "portfolio_value_change_pct"
            if "portfolio_value_change_pct" in sample
            else "price_change_pct"
        )

        # Expected return
        expected_return = 0.0
        count = 0
        for key, prob in norm_probs.items():
            result = scenario_results.get(key)
            if result is None:
                continue
            expected_return += prob * result.get(ret_field, 0)
            count += 1

        if count == 0:
            return {
                "expected_return_pct": 0.0,
                "variance": 0.0,
                "std_dev_pct": 0.0,
                "sharpe_ratio_estimate": 0.0,
                "num_scenarios": 0,
            }

        # Variance
        variance = 0.0
        for key, prob in norm_probs.items():
            result = scenario_results.get(key)
            if result is None:
                continue
            ret = result.get(ret_field, 0)
            variance += prob * (ret - expected_return) ** 2

        std_dev = math.sqrt(variance)
        sharpe = expected_return / std_dev if std_dev > 0 else 0.0

        return {
            "expected_return_pct": round(expected_return, 4),
            "variance": round(variance, 6),
            "std_dev_pct": round(std_dev, 4),
            "sharpe_ratio_estimate": round(sharpe, 4),
            "num_scenarios": count,
        }

    @staticmethod
    def format_scenario_table(
        scenario_results: dict[str, dict[str, Any]],
        bond_name: str = "",
    ) -> str:
        """Format scenario results as a human-readable table.

        Parameters
        ----------
        scenario_results :
            Output from :meth:`analyze_bond` or :meth:`analyze_portfolio`.
        bond_name :
            Optional label displayed in the table header.

        Returns
        -------
        str
            Multi-line table string.
        """
        if not scenario_results:
            return "(no data)"

        # Detect result type
        sample = next(iter(scenario_results.values()), {})
        if "portfolio_value_change_pct" in sample:
            return ScenarioAnalyzer._format_portfolio_table(
                scenario_results, bond_name,
            )
        return ScenarioAnalyzer._format_bond_table(
            scenario_results, bond_name,
        )

    # ------------------------------------------------------------------ #
    #  Formatting internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _format_bond_table(
        scenario_results: dict[str, dict[str, Any]],
        bond_name: str = "",
    ) -> str:
        """Pretty-print single-bond scenario results."""
        lines: list[str] = []
        title = (
            f"Scenario analysis: {bond_name}"
            if bond_name
            else "Scenario analysis"
        )
        lines.append(title)
        lines.append("-" * 75)
        header = (
            f"{'Scenario':<30} {'Price change':>12} "
            f"{'P&L (RUB)':>12} {'New price':>10} {'Likelihood':>14}"
        )
        lines.append(header)
        lines.append("-" * 75)

        for key, result in scenario_results.items():
            name = ScenarioAnalyzer._scenario_display_name(key)
            price_ch = result.get("price_change_pct", 0)
            pnl = result.get("pnl_rub", 0)
            new_price = result.get("new_price", 0)
            likelihood = result.get("probability_estimate", "")

            lines.append(
                f"{name:<30} {price_ch:>+10.2f}%  "
                f"{pnl:>+10.2f} {new_price:>8.2f}  {likelihood:>14}",
            )

        lines.append("-" * 75)
        return "\n".join(lines)

    @staticmethod
    def _format_portfolio_table(
        scenario_results: dict[str, dict[str, Any]],
        portfolio_name: str = "",
    ) -> str:
        """Pretty-print portfolio scenario results."""
        lines: list[str] = []
        title = (
            f"Portfolio scenario analysis: {portfolio_name}"
            if portfolio_name
            else "Portfolio scenario analysis"
        )
        lines.append(title)
        lines.append("-" * 95)
        header = (
            f"{'Scenario':<30} {'Portfolio change':>16} "
            f"{'Total P&L':>12} {'Worst bond':>20} {'Best bond':>20}"
        )
        lines.append(header)
        lines.append("-" * 95)

        for key, result in scenario_results.items():
            name = ScenarioAnalyzer._scenario_display_name(key)
            port_change = result.get("portfolio_value_change_pct", 0)
            total_pnl = result.get("total_pnl", 0)
            worst = result.get("worst_bond", {}).get("name", "-")
            best = result.get("best_bond", {}).get("name", "-")

            worst_str = worst if len(worst) <= 18 else worst[:16] + ".."
            best_str = best if len(best) <= 18 else best[:16] + ".."

            lines.append(
                f"{name:<30} {port_change:>+14.2f}% "
                f"{total_pnl:>+10.2f} {worst_str:>20} {best_str:>20}",
            )

        lines.append("-" * 95)
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    #  Probability helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _default_probabilities(
        scenario_results: dict[str, dict[str, Any]],
    ) -> dict[str, float]:
        """Build a reasonable probability distribution for the given scenarios.

        Rules:
            * ``base`` → 50 %
            * ``rate_down_*`` → 15 %  (split equally)
            * ``rate_up_*`` → 15 %    (split equally)
            * ``spread_*`` → 10 %     (split equally)
            * ``recession`` → 10 %
            * anything else → remaining probability split equally
        """
        keys = list(scenario_results.keys())
        prob: dict[str, float] = {}

        # Classify keys
        rate_down_keys = [k for k in keys if k.startswith("rate_down")]
        rate_up_keys = [k for k in keys if k.startswith("rate_up")]
        spread_keys = [k for k in keys if "spread" in k]
        remaining = [k for k in keys if k not in (
            ["base"]
            + rate_down_keys
            + rate_up_keys
            + spread_keys
            + ["recession"]
        )]

        consumed = 0.0

        if "base" in keys:
            prob["base"] = 0.50
            consumed += 0.50

        if rate_down_keys:
            share = 0.15 / len(rate_down_keys)
            for k in rate_down_keys:
                prob[k] = share
            consumed += 0.15

        if rate_up_keys:
            share = 0.15 / len(rate_up_keys)
            for k in rate_up_keys:
                prob[k] = share
            consumed += 0.15

        if spread_keys:
            share = 0.10 / len(spread_keys)
            for k in spread_keys:
                prob[k] = share
            consumed += 0.10

        if "recession" in keys:
            prob["recession"] = 0.10
            consumed += 0.10

        # Remaining scenarios get leftover probability
        leftover = max(0.0, 1.0 - consumed)
        if remaining and leftover > 0:
            share = leftover / len(remaining)
            for k in remaining:
                prob[k] = share
        else:
            for k in remaining:
                prob[k] = 0.0

        return prob
