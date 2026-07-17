"""
Bond portfolio optimization and risk budgeting.
Uses DTS (Duration x Z-spread) as the primary risk measure.
All methods are static — no instance state.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.optimize import minimize


class PortfolioOptimizer:
    """Bond portfolio optimization and risk budgeting."""

    @staticmethod
    def _convexity_approx(duration: float, ytm: float) -> float:
        """
        Approximate convexity when not provided.

        Convexity ~ (Duration^2 + Duration) / (1 + YTM)^2

        This is the Macaulay-duration-based approximation valid for
        small yield changes.
        """
        if duration <= 0:
            return 0.0
        ytm_dec = max(ytm / 100.0, -0.999)  # guard against YTM < -100%
        return (duration * duration + duration) / ((1.0 + ytm_dec) ** 2)

    # ------------------------------------------------------------------
    # Risk Parity
    # ------------------------------------------------------------------

    @staticmethod
    def risk_parity(
        bonds: list[dict[str, Any]],
        target_vol: float | None = None,
    ) -> dict:
        """
        Risk parity optimisation: equalise risk contribution of each bond.

        Risk contribution[i] = w_i * DTS_i  /  sum(w_j * DTS_j)

        where DTS_i = ModifiedDuration_i * Z_spread_i  (Duration Times
        Spread, the key risk proxy for bonds).

        Constraints:
        - sum(w_i) = 1
        - 0 <= w_i <= 0.30 (long-only, max 30 % per issuer)

        Parameters
        ----------
        bonds :
            Each dict must contain *name*, *modified_duration* and
            *z_spread* (in %).  *weight* is ignored (initial guess).
        target_vol : float or None
            If given, the resulting portfolio DTS is scaled (via
            linear leverage) to this level while preserving the risk-
            parity proportions.  The sum(w) may deviate from 1.

        Returns
        -------
        dict with keys:
            weights, risk_contributions, duration_target, dts_target.
        """
        n = len(bonds)
        if n == 0:
            return {
                "weights": {},
                "risk_contributions": {},
                "duration_target": 0.0,
                "dts_target": 0.0,
            }

        names = [b["name"] for b in bonds]
        durations = np.array([b.get("modified_duration", 0.0) for b in bonds],
                             dtype=np.float64)
        spreads = np.array([b.get("z_spread", 0.0) for b in bonds],
                           dtype=np.float64)

        # DTS = Duration * Spread  (spread is already in %)
        dts = durations * spreads
        dts = np.clip(dts, 1e-8, None)

        # ---- optimisation ----
        w0 = np.ones(n) / n
        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
        bounds = [(0.0, 0.30)] * n

        def _risk_contrib(w: np.ndarray) -> np.ndarray:
            total = np.sum(w * dts)
            if total <= 0:
                return np.ones(n) / n
            return w * dts / total

        def _objective(w: np.ndarray) -> float:
            rc = _risk_contrib(w)
            target = 1.0 / n
            return float(np.sum((rc - target) ** 2) * 1e4)

        result = minimize(
            _objective,
            w0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-12},
        )

        w = np.clip(result.x if result.success else w0, 0.0, None)
        w = w / np.sum(w)

        # optional scaling to target volatility
        if target_vol is not None and target_vol > 0:
            current_dts = float(np.sum(w * dts))
            if current_dts > 0:
                scale = target_vol / current_dts
                w = w * scale
                w = np.clip(w, 0.0, 0.30)

        rc = _risk_contrib(w)
        port_dur = float(np.sum(w * durations))
        port_dts = float(np.sum(w * dts))

        return {
            "weights": {names[i]: round(float(w[i]), 6) for i in range(n)},
            "risk_contributions": {
                names[i]: round(float(rc[i]), 6) for i in range(n)
            },
            "duration_target": round(port_dur, 4),
            "dts_target": round(port_dts, 4),
        }

    # ------------------------------------------------------------------
    # Mean-Variance
    # ------------------------------------------------------------------

    @staticmethod
    def mean_variance(
        bonds: list[dict[str, Any]],
        risk_aversion: float = 1.0,
        max_duration: float | None = None,
        min_yield: float | None = None,
    ) -> dict:
        """
        Mean-variance optimisation for a bond portfolio.

        Objective
        ---------
        maximise    w' * mu - 0.5 * gamma * w' * Sigma * w

        where
        - mu = YTM (as a proxy for expected return)
        - Sigma = covariance matrix estimated from DTS with a constant
          cross-bond correlation (rho = 0.3 by default)
        - gamma = risk_aversion parameter

        Constraints
        -----------
        - sum(w) = 1,  w >= 0  (long-only)
        - max_duration    if provided
        - min_yield       if provided

        Parameters
        ----------
        bonds :
            Each dict must contain *name*, *ytm* (in %),
            *modified_duration*, *z_spread* (in %).
        risk_aversion : float
            Higher values produce more conservative allocations.
        max_duration : float or None
            Upper bound on portfolio modified duration (years).
        min_yield : float or None
            Lower bound on portfolio YTM (%).

        Returns
        -------
        dict with keys:
            weights, expected_return (%), portfolio_risk (%),
            portfolio_duration, portfolio_dts, sharpe_ratio,
            efficient_frontier_sample (list of {return_pct, risk_pct}).
        """
        n = len(bonds)
        if n == 0:
            return {
                "weights": {},
                "expected_return": 0.0,
                "portfolio_risk": 0.0,
                "portfolio_duration": 0.0,
                "portfolio_dts": 0.0,
                "sharpe_ratio": 0.0,
                "efficient_frontier_sample": [],
            }

        names = [b["name"] for b in bonds]
        ytm = np.array([b.get("ytm", 0.0) for b in bonds], dtype=np.float64)
        durations = np.array(
            [b.get("modified_duration", 0.0) for b in bonds], dtype=np.float64
        )
        spreads = np.array(
            [b.get("z_spread", 0.0) for b in bonds], dtype=np.float64
        )

        dts = np.clip(durations * spreads, 1e-8, None)

        # expected returns in decimal
        mu = ytm / 100.0

        # volatility proxy (decimal) from DTS
        sigma = dts / 100.0

        # covariance with constant correlation rho
        rho = 0.3
        cov = np.full((n, n), rho)
        np.fill_diagonal(cov, 1.0)
        cov = cov * np.outer(sigma, sigma)

        w0 = np.ones(n) / n
        bounds = [(0.0, 1.0)] * n
        constraints: list[dict] = [
            {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}
        ]

        if max_duration is not None:
            constraints.append({
                "type": "ineq",
                "fun": lambda w: max_duration - np.sum(w * durations),
            })

        if min_yield is not None:
            constraints.append({
                "type": "ineq",
                "fun": lambda w: np.sum(w * ytm) - min_yield,
            })

        def _neg_obj(w: np.ndarray) -> float:
            ret = np.dot(w, mu)
            risk = w @ cov @ w
            return float(-(ret - 0.5 * risk_aversion * risk))

        result = minimize(
            _neg_obj,
            w0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-12},
        )

        w = np.clip(result.x if result.success else w0, 0.0, None)
        w = w / np.sum(w)

        port_ret = float(np.dot(w, mu))
        port_risk = float(np.sqrt(w @ cov @ w))
        port_dur = float(np.sum(w * durations))
        port_dts = float(np.sum(w * dts))
        sharpe = port_ret / port_risk if port_risk > 0 else 0.0

        # ---- efficient frontier sample ----
        frontier: list[dict[str, float]] = []
        target_rets = np.linspace(float(np.min(mu)), float(np.max(mu)), 10)

        for tr in target_rets:
            cons: list[dict] = [
                {"type": "eq", "fun": lambda w: np.sum(w) - 1.0},
                {
                    "type": "eq",
                    "fun": lambda w, t=tr: np.dot(w, mu) - t,
                },
            ]
            res = minimize(
                lambda w: float(w @ cov @ w),
                w0,
                method="SLSQP",
                bounds=bounds,
                constraints=cons,
                options={"maxiter": 1000},
            )
            if res.success:
                risk_val = float(np.sqrt(float(res.x @ cov @ res.x)))
                frontier.append({
                    "return_pct": round(float(tr) * 100, 4),
                    "risk_pct": round(risk_val * 100, 4),
                })

        return {
            "weights": {names[i]: round(float(w[i]), 6) for i in range(n)},
            "expected_return": round(port_ret * 100, 4),
            "portfolio_risk": round(port_risk * 100, 4),
            "portfolio_duration": round(port_dur, 4),
            "portfolio_dts": round(port_dts, 4),
            "sharpe_ratio": round(sharpe, 4),
            "efficient_frontier_sample": frontier,
        }

    # ------------------------------------------------------------------
    # Barbell vs Bullet
    # ------------------------------------------------------------------

    @staticmethod
    def barbell_vs_bullet(
        bonds: list[dict[str, Any]],
        target_duration: float,
    ) -> dict:
        """
        Compare barbell (short + long) and bullet (concentrated) strategies.

        - **Barbell**:  short-duration bonds (D < target/2)  +  long-duration
          bonds (D > target * 1.5), weighted to hit *target_duration*.
        - **Bullet**:   bonds whose duration is within 20 % of the target,
          equally weighted.

        The comparison uses convexity as the primary metric — barbell
        typically offers higher convexity at the same duration.

        Parameters
        ----------
        bonds :
            Each dict must contain *name*, *ytm* (%), *modified_duration*,
            *z_spread* (%).  Optionally *convexity*; if absent it is
            approximated.
        target_duration : float
            Target portfolio modified duration in years.

        Returns
        -------
        dict with keys:
            barbell     {bonds, duration, convexity, dts, expected_return}
            bullet      {bonds, duration, convexity, dts, expected_return}
            recommendation (str)
            advantage_pct  (%) — convexity advantage of barbell over bullet.
        """
        if not bonds:
            return {
                "barbell": {
                    "bonds": [], "duration": 0.0, "convexity": 0.0,
                    "dts": 0.0, "expected_return": 0.0,
                },
                "bullet": {
                    "bonds": [], "duration": 0.0, "convexity": 0.0,
                    "dts": 0.0, "expected_return": 0.0,
                },
                "recommendation": "N/A -- no bonds provided",
                "advantage_pct": 0.0,
            }

        target = max(target_duration, 0.01)

        short_pool = [
            b for b in bonds
            if b.get("modified_duration", 0) < target / 2.0
        ]
        long_pool = [
            b for b in bonds
            if b.get("modified_duration", 0) > target * 1.5
        ]
        bullet_pool = [
            b for b in bonds
            if abs(b.get("modified_duration", 0) - target) / target <= 0.2
        ]

        def _pool_stats(pool: list[dict],
                        weights: list[float]) -> dict:
            dur = sum(w * b.get("modified_duration", 0.0)
                      for w, b in zip(weights, pool))
            ytm_avg = sum(w * b.get("ytm", 0.0)
                          for w, b in zip(weights, pool))
            spread = sum(w * b.get("z_spread", 0.0)
                         for w, b in zip(weights, pool))

            conv = 0.0
            for w, b in zip(weights, pool):
                c = b.get("convexity")
                if c is None:
                    c = PortfolioOptimizer._convexity_approx(
                        b.get("modified_duration", 0.0),
                        b.get("ytm", 0.0),
                    )
                conv += w * c

            return {
                "bonds": [b["name"] for b in pool],
                "duration": round(dur, 4),
                "convexity": round(conv, 4),
                "dts": round(dur * spread, 4),
                "expected_return": round(ytm_avg, 4),
            }

        # ---- Barbell construction ----
        if short_pool and long_pool:
            short_avg = float(np.mean(
                [b.get("modified_duration", 0.0) for b in short_pool]
            ))
            long_avg = float(np.mean(
                [b.get("modified_duration", 0.0) for b in long_pool]
            ))

            if abs(long_avg - short_avg) > 1e-6:
                w_short = (long_avg - target) / (long_avg - short_avg)
                w_short = float(np.clip(w_short, 0.0, 1.0))
                w_long = 1.0 - w_short
            else:
                w_short = w_long = 0.5

            barbell_pool = short_pool + long_pool
            barbell_weights = (
                [w_short / len(short_pool)] * len(short_pool)
                + [w_long / len(long_pool)] * len(long_pool)
            )
            barbell_stats = _pool_stats(barbell_pool, barbell_weights)
        else:
            barbell_stats = {
                "bonds": [], "duration": 0.0, "convexity": 0.0,
                "dts": 0.0, "expected_return": 0.0,
            }

        # ---- Bullet construction ----
        if bullet_pool:
            bw = [1.0 / len(bullet_pool)] * len(bullet_pool)
            bullet_stats = _pool_stats(bullet_pool, bw)
        else:
            bullet_stats = {
                "bonds": [], "duration": 0.0, "convexity": 0.0,
                "dts": 0.0, "expected_return": 0.0,
            }

        # ---- Recommendation ----
        if (barbell_stats["convexity"] > 0 and bullet_stats["convexity"] > 0):
            adv = barbell_stats["convexity"] - bullet_stats["convexity"]
            adv_pct = (
                (adv / bullet_stats["convexity"]) * 100.0
                if bullet_stats["convexity"] > 0 else 0.0
            )
            if adv > 0:
                recommendation = (
                    "Barbell offers higher convexity at same duration -- "
                    "better protection against yield shifts"
                )
            else:
                recommendation = (
                    "Bullet is preferable -- similar convexity with "
                    "simpler implementation"
                )
        elif barbell_stats["convexity"] > 0:
            adv_pct = 100.0
            recommendation = "Only barbell is feasible with available bonds"
        elif bullet_stats["convexity"] > 0:
            adv_pct = -100.0
            recommendation = "Only bullet is feasible with available bonds"
        else:
            adv_pct = 0.0
            recommendation = "Insufficient data for comparison"

        return {
            "barbell": barbell_stats,
            "bullet": bullet_stats,
            "recommendation": recommendation,
            "advantage_pct": round(adv_pct, 2),
        }

    # ------------------------------------------------------------------
    # Sector Allocation
    # ------------------------------------------------------------------

    @staticmethod
    def sector_allocation(
        bonds: list[dict[str, Any]],
        max_sector_pct: float = 0.40,
    ) -> dict:
        """
        Optimise sector allocation with diversification constraints.

        Sector classification (expected in *sector* field):
            OFZ (sovereign), corporate_high (A+ and above),
            corporate_mid (BBB- to BBB+), corporate_low (below BBB).

        The objective balances portfolio YTM against a diagonal-risk
        penalty to avoid concentrating into a single bond within a
        sector.

        Constraints
        -----------
        - sum(w) = 1, w >= 0
        - sum of bond weights in any sector <= max_sector_pct

        Parameters
        ----------
        bonds :
            Each dict must contain *name*, *sector*, *ytm* (%),
            *modified_duration*, *z_spread* (%).
        max_sector_pct : float
            Maximum aggregate weight per sector (default 0.40 = 40 %).

        Returns
        -------
        dict with keys:
            sector_weights       {sector_name: weight},
            bond_weights         {bond_name: weight},
            diversification_ratio,
            concentration_hhi    (Herfindahl-Hirschman Index).
        """
        n = len(bonds)
        if n == 0:
            return {
                "sector_weights": {},
                "bond_weights": {},
                "diversification_ratio": 0.0,
                "concentration_hhi": 0.0,
            }

        names = [b["name"] for b in bonds]
        sectors = [b.get("sector", "unknown") for b in bonds]
        ytm = np.array([b.get("ytm", 0.0) for b in bonds], dtype=np.float64)
        durations = np.array(
            [b.get("modified_duration", 0.0) for b in bonds], dtype=np.float64
        )
        spreads = np.array(
            [b.get("z_spread", 0.0) for b in bonds], dtype=np.float64
        )
        dts = np.clip(durations * spreads, 1e-8, None)

        unique_sectors = sorted(set(sectors))
        sector_to_idx: dict[str, list[int]] = {
            s: [i for i, s2 in enumerate(sectors) if s2 == s]
            for s in unique_sectors
        }

        # returns (decimal) and risk (DTS in decimal)
        mu = ytm / 100.0
        sigma = dts / 100.0

        w0 = np.ones(n) / n
        bounds = [(0.0, 1.0)] * n
        constraints: list[dict] = [
            {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}
        ]

        for s, idx_list in sector_to_idx.items():
            constraints.append({
                "type": "ineq",
                "fun": lambda w, idx=idx_list: max_sector_pct - np.sum(w[idx]),
            })

        def _neg_obj(w: np.ndarray) -> float:
            ret = np.dot(w, mu)
            # diagonal risk penalty to avoid single-bond concentration
            risk = float(np.sqrt(np.sum((w * sigma) ** 2)))
            return float(-(ret - 0.05 * risk))

        result = minimize(
            _neg_obj,
            w0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-12},
        )

        w = np.clip(result.x if result.success else w0, 0.0, None)
        w = w / np.sum(w)

        # sector-level aggregation
        sector_weights: dict[str, float] = {}
        for s, idx_list in sector_to_idx.items():
            sector_weights[s] = round(float(np.sum(w[idx_list])), 6)

        # concentration
        hhi = float(np.sum(w ** 2))

        # diversification ratio  =  sum(w_i * sigma_i) / sigma_portfolio
        weighted_avg_vol = float(np.sum(w * sigma))
        port_vol = float(np.sqrt(float(np.sum((w * sigma) ** 2))))
        div_ratio = (
            weighted_avg_vol / port_vol if port_vol > 0 else 1.0
        )

        return {
            "sector_weights": sector_weights,
            "bond_weights": {
                names[i]: round(float(w[i]), 6) for i in range(n)
            },
            "diversification_ratio": round(div_ratio, 4),
            "concentration_hhi": round(hhi, 4),
        }

    # ------------------------------------------------------------------
    # Efficiency Report
    # ------------------------------------------------------------------

    @staticmethod
    def efficiency_report(
        portfolio: list[dict[str, Any]],
    ) -> dict:
        """
        Calculate portfolio-level efficiency metrics.

        Metrics
        -------
        - **Portfolio YTM**       — weighted average yield to maturity (%).
        - **Portfolio Duration**  — weighted average modified duration.
        - **Portfolio DTS**       — sum(w_i * D_i * spread_i).
        - **Portfolio Convexity** — weighted average convexity.
        - **Concentration HHI**   — Herfindahl-Hirschman Index  =  sum(w_i^2).
          1/N <= HHI <= 1.  Lower is more diversified.
        - **Effective N**         — 1 / HHI  (diversification measure).
        - **Sharpe-like Ratio**   — YTM / DTS  (yield per unit of DTS risk).

        Parameters
        ----------
        portfolio :
            Each dict must contain *name*, *weight*, *ytm* (%),
            *modified_duration*, *z_spread* (%).  Optionally *convexity*;
            if absent it is approximated.

        Returns
        -------
        dict with all the above metrics.
        """
        if not portfolio:
            return {
                "portfolio_ytm": 0.0,
                "portfolio_duration": 0.0,
                "portfolio_convexity": 0.0,
                "portfolio_dts": 0.0,
                "concentration_hhi": 0.0,
                "effective_num_bonds": 0.0,
                "sharpe_like_ratio": 0.0,
                "num_bonds": 0,
            }

        weights = np.array(
            [b.get("weight", 0.0) for b in portfolio], dtype=np.float64
        )
        w_sum = float(np.sum(weights))
        if w_sum > 0:
            weights = weights / w_sum
        else:
            weights = np.ones(len(portfolio)) / len(portfolio)

        ytm = np.array(
            [b.get("ytm", 0.0) for b in portfolio], dtype=np.float64
        )
        durations = np.array(
            [b.get("modified_duration", 0.0) for b in portfolio],
            dtype=np.float64,
        )
        spreads = np.array(
            [b.get("z_spread", 0.0) for b in portfolio], dtype=np.float64
        )

        convexities_list = []
        for b in portfolio:
            c = b.get("convexity")
            if c is None:
                c = PortfolioOptimizer._convexity_approx(
                    b.get("modified_duration", 0.0),
                    b.get("ytm", 0.0),
                )
            convexities_list.append(c)
        convexities = np.array(convexities_list, dtype=np.float64)

        port_ytm = float(np.sum(weights * ytm))
        port_dur = float(np.sum(weights * durations))
        port_dts = float(np.sum(weights * durations * spreads))
        port_conv = float(np.sum(weights * convexities))

        hhi = float(np.sum(weights ** 2))
        eff_n = 1.0 / hhi if hhi > 0 else float(len(portfolio))

        # Sharpe-like: yield per unit of DTS risk
        sharpe_like = port_ytm / port_dts if port_dts > 0 else 0.0

        return {
            "portfolio_ytm": round(port_ytm, 4),
            "portfolio_duration": round(port_dur, 4),
            "portfolio_convexity": round(port_conv, 4),
            "portfolio_dts": round(port_dts, 4),
            "concentration_hhi": round(hhi, 4),
            "effective_num_bonds": round(eff_n, 2),
            "sharpe_like_ratio": round(sharpe_like, 4),
            "num_bonds": len(portfolio),
        }
