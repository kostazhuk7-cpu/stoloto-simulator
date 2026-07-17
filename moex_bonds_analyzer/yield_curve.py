"""
Yield curve construction using Nelson-Siegel-Svensson model.

Fits a smooth parsimonious yield curve to MOEX OFZ bond data using
scipy.optimize.minimize with multi-start for robustness.

The NSS model represents the instantaneous forward rate as:
    f(t) = β₀ + β₁·exp(-t/τ₁) + β₂·(t/τ₁)·exp(-t/τ₁) + β₃·(t/τ₂)·exp(-t/τ₂)

And the corresponding spot rate at maturity t:
    y(t) = β₀ + β₁·(1-exp(-t/τ₁))/(t/τ₁)
         + β₂·((1-exp(-t/τ₁))/(t/τ₁) - exp(-t/τ₁))
         + β₃·((1-exp(-t/τ₂))/(t/τ₂) - exp(-t/τ₂))

References:
    - Nelson & Siegel (1987) "Parsimonious Modeling of Yield Curves"
    - Svensson (1994) "Estimating and Interpreting Forward Interest Rates"
"""
from __future__ import annotations

import math
import warnings
from typing import Any, Optional

import numpy as np
from scipy.optimize import minimize


class YieldCurve:
    """Nelson-Siegel-Svensson yield curve model.

    Provides static methods to fit NSS curves to observed bond yields,
    evaluate spot/forward rates, and construct curves from OFZ bond data.

    All public methods return dicts with full result metadata.
    """

    NSS_PARAMS: list[str] = ["beta0", "beta1", "beta2", "beta3", "tau1", "tau2"]
    """Ordered list of NSS parameter names."""

    # ------------------------------------------------------------------
    # Core NSS formula
    # ------------------------------------------------------------------

    @staticmethod
    def nss_rate(
        t: float,
        beta0: float,
        beta1: float,
        beta2: float,
        beta3: float,
        tau1: float,
        tau2: float,
    ) -> float:
        """Nelson-Siegel-Svensson spot rate at maturity *t*.

        The formula gives the continuously-compounded zero-coupon rate
        for maturity *t* years.

        For t → 0, the limit is β₀ + β₁ (the instantaneous short rate).

        Parameters
        ----------
        t : float
            Maturity in years (non-negative).
        beta0, beta1, beta2, beta3 : float
            Level, slope, and curvature coefficients.
        tau1, tau2 : float
            Decay parameters (positive time constants).

        Returns
        -------
        float
            Spot rate in decimal (e.g. 0.145 for 14.5%).
        """
        # Limit at t = 0: short rate = beta0 + beta1
        if t < 1e-10:
            return beta0 + beta1

        x1 = t / tau1
        x2 = t / tau2

        # Use expm1 for better precision on small arguments
        exp_x1 = math.exp(-x1)
        exp_x2 = math.exp(-x2)

        # Nelson-Siegel factor: (1 - exp(-x)) / x
        if x1 > 1e-12:
            ns_factor1 = (1.0 - exp_x1) / x1
        else:
            # Series: 1 - x/2 + x²/6 - ...
            ns_factor1 = 1.0 - x1 / 2.0 + x1 * x1 / 6.0

        if x2 > 1e-12:
            ns_factor2 = (1.0 - exp_x2) / x2
        else:
            ns_factor2 = 1.0 - x2 / 2.0 + x2 * x2 / 6.0

        # Svensson curvature factors
        curv_factor1 = ns_factor1 - exp_x1  # (1-exp(-x1))/x1 - exp(-x1)
        curv_factor2 = ns_factor2 - exp_x2  # (1-exp(-x2))/x2 - exp(-x2)

        return (
            beta0
            + beta1 * ns_factor1
            + beta2 * curv_factor1
            + beta3 * curv_factor2
        )

    @staticmethod
    def nss_rate_vec(
        params: np.ndarray,
        maturities: np.ndarray,
    ) -> np.ndarray:
        """Vectorised NSS rate computation (used internally by *fit*).

        Parameters
        ----------
        params : np.ndarray[6]
            [beta0, beta1, beta2, beta3, tau1, tau2].
        maturities : np.ndarray
            Array of maturities in years.

        Returns
        -------
        np.ndarray
            Spot rates in decimal.
        """
        beta0, beta1, beta2, beta3, tau1, tau2 = params

        # Handle near-zero maturities via the limit
        result = np.full_like(maturities, beta0 + beta1, dtype=float)

        mask = maturities > 1e-10
        t = maturities[mask]
        if len(t) == 0:
            return result

        x1 = t / tau1
        x2 = t / tau2

        exp_x1 = np.exp(-x1)
        exp_x2 = np.exp(-x2)

        # Nelson-Siegel factor: (1 - e^{-x}) / x
        # Use np.expm1 = exp(x) - 1 → -expm1(-x) = 1 - exp(-x)
        ns1 = np.where(x1 > 1e-12, -np.expm1(-x1) / x1, 1.0)
        ns2 = np.where(x2 > 1e-12, -np.expm1(-x2) / x2, 1.0)

        curv1 = ns1 - exp_x1
        curv2 = ns2 - exp_x2

        result[mask] = (
            beta0 + beta1 * ns1 + beta2 * curv1 + beta3 * curv2
        )
        return result

    # ------------------------------------------------------------------
    # Optimisation objective
    # ------------------------------------------------------------------

    @staticmethod
    def _nss_objective(
        params: np.ndarray,
        maturities: np.ndarray,
        yields: np.ndarray,
        weights: np.ndarray | None,
    ) -> float:
        """Weighted sum of squared residuals for NSS fitting.

        Returns a large penalty when *tau* parameters are non-positive
        to keep the optimiser away from invalid regions.
        """
        tau1, tau2 = params[4], params[5]
        if tau1 <= 0.0 or tau2 <= 0.0:
            return 1e10 * (1.0 + abs(tau1) + abs(tau2))

        fitted = YieldCurve.nss_rate_vec(params, maturities)
        residuals = yields - fitted

        if weights is not None:
            return float(np.sum(weights * residuals ** 2))
        return float(np.sum(residuals ** 2))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def fit(
        maturities: list[float],
        yields: list[float],
        weights: list[float] | None = None,
    ) -> dict:
        """Fit the Nelson-Siegel-Svensson curve to observed yields.

        Uses ``scipy.optimize.minimize`` with L-BFGS-B (bounded) as the
        primary solver.  Falls back to Nelder-Mead and then multi-start
        random restarts if convergence fails.

        Parameters
        ----------
        maturities : list[float]
            Time to maturity in years for each observation.
        yields : list[float]
            Observed yields in decimal (e.g. ``0.145`` for 14.5%).
        weights : list[float] | None
            Optional per-observation weights (e.g. by liquidity or
            inverse bid-ask spread).  Weights are normalised so that
            their sum equals the number of observations.

        Returns
        -------
        dict
            Fitted parameters (beta0–beta3, tau1–tau2), goodness-of-fit
            metrics (R², RMSE, AIC, BIC), fitted values, residuals, and
            convergence diagnostics.
        """
        # ── Input validation ──────────────────────────────────────────
        n = len(maturities)
        if n != len(yields):
            return {
                "success": False,
                "error": f"Length mismatch: maturities={n}, yields={len(yields)}",
            }
        if n < 4:
            return {
                "success": False,
                "error": f"NSS needs at least 4 data points (got {n})",
            }

        mat_arr = np.asarray(maturities, dtype=float)
        yld_arr = np.asarray(yields, dtype=float)

        # Normalise weights so sum(weights) = n (neutral scaling)
        wgt_arr: np.ndarray | None = None
        if weights is not None:
            if len(weights) != n:
                return {
                    "success": False,
                    "error": f"Weights length {len(weights)} != {n}",
                }
            wgt_arr = np.asarray(weights, dtype=float)
            w_sum = np.sum(wgt_arr)
            if w_sum > 0.0:
                wgt_arr = wgt_arr / w_sum * n
            else:
                wgt_arr = None

        # ── Initial guess & bounds ────────────────────────────────────
        # Sensible starting point for Russian OFZ market (rates ~14-15%)
        x0 = np.array([0.145, -0.02, 0.01, 0.01, 2.0, 5.0], dtype=float)

        bounds = [
            (-0.5, 1.0),   # beta0: long-term level
            (-0.5, 0.5),   # beta1: short-term slope
            (-0.5, 0.5),   # beta2: medium-term curvature
            (-0.5, 0.5),   # beta3: long-term curvature
            (0.01, 30.0),  # tau1: decay (must be > 0)
            (0.01, 30.0),  # tau2: decay (must be > 0)
        ]

        opt_kwargs = dict(
            args=(mat_arr, yld_arr, wgt_arr),
            bounds=bounds,
        )

        # ── Multi-start strategy ──────────────────────────────────────
        best_result: Any = None
        best_fun: float = float("inf")

        def _update_best(res: Any) -> None:
            nonlocal best_result, best_fun
            if res.fun < best_fun:
                best_result = res
                best_fun = res.fun

        # Pass 1 — L-BFGS-B (gradient-based, bounded)
        try:
            res = minimize(
                YieldCurve._nss_objective,
                x0,
                method="L-BFGS-B",
                options={"maxiter": 5000, "ftol": 1e-12, "gtol": 1e-12},
                **opt_kwargs,
            )
            _update_best(res)
        except Exception:
            pass

        # Pass 2 — Nelder-Mead from x0 (derivative-free, robust)
        try:
            res = minimize(
                YieldCurve._nss_objective,
                x0,
                method="Nelder-Mead",
                options={"maxiter": 10_000, "xatol": 1e-8, "fatol": 1e-8},
                **opt_kwargs,
            )
            _update_best(res)
        except Exception:
            pass

        # Pass 3 — random restarts with Nelder-Mead
        rng = np.random.default_rng(seed=42)
        for _ in range(8):
            # Perturb each parameter by ±50 %
            factor = 1.0 + rng.uniform(-0.5, 0.5, size=6)
            x_pert = x0 * factor
            x_pert[4] = max(0.1, x_pert[4])  # tau1 > 0
            x_pert[5] = max(0.1, x_pert[5])  # tau2 > 0

            try:
                res = minimize(
                    YieldCurve._nss_objective,
                    x_pert,
                    method="Nelder-Mead",
                    options={"maxiter": 5000, "xatol": 1e-8, "fatol": 1e-8},
                    **opt_kwargs,
                )
                _update_best(res)
            except Exception:
                continue

        # ── Assemble result ───────────────────────────────────────────
        if best_result is None:
            return {
                "success": False,
                "error": "All optimisation attempts failed",
            }

        beta0_f, beta1_f, beta2_f, beta3_f, tau1_f, tau2_f = best_result.x

        fitted_all = YieldCurve.nss_rate_vec(best_result.x, mat_arr)
        residuals = yld_arr - fitted_all

        ss_res = float(np.sum(residuals ** 2))
        ss_tot = float(np.sum((yld_arr - np.mean(yld_arr)) ** 2))
        r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-15 else 0.0
        rmse = float(np.sqrt(np.mean(residuals ** 2)))

        # AIC / BIC (assuming normally distributed errors)
        k = 6
        if ss_res > 1e-15 and n > 1:
            sigma2_hat = ss_res / n
            log_lik = -n / 2.0 * (
                math.log(2.0 * math.pi * sigma2_hat) + 1.0
            )
            aic = 2.0 * k - 2.0 * log_lik
            bic = float(k * math.log(n) - 2.0 * log_lik)
        else:
            aic = None
            bic = None

        return {
            "success": best_result.success,
            "converged": best_result.success,
            "optimizer_message": best_result.message if hasattr(best_result, "message") else "",
            "optimizer_method": "multi-start",
            "beta0": round(float(beta0_f), 6),
            "beta1": round(float(beta1_f), 6),
            "beta2": round(float(beta2_f), 6),
            "beta3": round(float(beta3_f), 6),
            "tau1": round(float(tau1_f), 6),
            "tau2": round(float(tau2_f), 6),
            "r_squared": round(r_squared, 6),
            "rmse": round(rmse, 6),
            "aic": round(aic, 4) if aic is not None else None,
            "bic": round(bic, 4) if bic is not None else None,
            "fitted_values": [round(float(v), 6) for v in fitted_all],
            "residuals": [round(float(r), 6) for r in residuals],
            "maturities": maturities,
            "observed_yields": yields,
            "num_observations": n,
            "objective_value": round(float(best_result.fun), 8),
        }

    @staticmethod
    def get_rate(curve_params: dict, maturity: float) -> float:
        """Evaluate the fitted curve at a single maturity.

        Parameters
        ----------
        curve_params : dict
            A result dict from ``fit()`` or ``build_from_ofz()`` containing
            the six NSS parameters.
        maturity : float
            Time to maturity in years (non-negative).

        Returns
        -------
        float
            Spot rate in decimal.
        """
        return YieldCurve.nss_rate(
            maturity,
            curve_params.get("beta0", 0.0),
            curve_params.get("beta1", 0.0),
            curve_params.get("beta2", 0.0),
            curve_params.get("beta3", 0.0),
            curve_params.get("tau1", 1.0),
            curve_params.get("tau2", 5.0),
        )

    @staticmethod
    def get_forward_rate(curve_params: dict, t1: float, t2: float) -> float:
        """Forward rate between *t1* and *t2*.

        Uses continuously-compounded forward rate:
            f(t₁, t₂) = [s₂·t₂ − s₁·t₁] / (t₂ − t₁)

        where *sᵢ* = ``get_rate(curve_params, tᵢ)``.

        Parameters
        ----------
        curve_params : dict
            Fitted NSS parameters.
        t1 : float
            Start of forward period (years).
        t2 : float
            End of forward period (years, > t1).

        Returns
        -------
        float
            Continuously-compounded forward rate in decimal.
        """
        if t2 <= t1 + 1e-14:
            return YieldCurve.get_rate(curve_params, t1)

        s1 = YieldCurve.get_rate(curve_params, t1)
        s2 = YieldCurve.get_rate(curve_params, t2)
        return (s2 * t2 - s1 * t1) / (t2 - t1)

    @staticmethod
    def get_spot_rates(
        curve_params: dict,
        maturities: list[float],
    ) -> dict[float, float]:
        """Evaluate the fitted curve at multiple maturities.

        Parameters
        ----------
        curve_params : dict
            Fitted NSS parameters.
        maturities : list[float]
            List of maturities in years.

        Returns
        -------
        dict[float, float]
            Mapping ``{maturity: spot_rate}``.
        """
        params = np.array([
            curve_params.get("beta0", 0.0),
            curve_params.get("beta1", 0.0),
            curve_params.get("beta2", 0.0),
            curve_params.get("beta3", 0.0),
            curve_params.get("tau1", 1.0),
            curve_params.get("tau2", 5.0),
        ])
        mats_arr = np.asarray(maturities, dtype=float)
        rates = YieldCurve.nss_rate_vec(params, mats_arr)
        return {m: round(float(r), 6) for m, r in zip(maturities, rates)}

    # ------------------------------------------------------------------
    # OFZ-specific builder
    # ------------------------------------------------------------------

    @staticmethod
    def build_from_ofz(ofz_data: list[dict]) -> dict:
        """Build a yield curve from OFZ bond data.

        Fits the NSS model directly to observed YTMs, with greater
        weight on shorter maturities to capture the front end of the
        curve more precisely.

        Parameters
        ----------
        ofz_data : list[dict]
            Each dict must contain:
                - ``maturity_years`` (float)
                - ``ytm`` (float, decimal e.g. 0.145 for 14.5%)
            And optionally:
                - ``coupon`` (float, annual rate in decimal)
                - ``price`` (float, clean price in % of face)

        Returns
        -------
        dict
            Combined result of ``fit()`` plus two extra keys:
                - ``bond_results``: per-bond fitted YTM, residual
                - ``spot_rates``: standard-tenor spot rates (3m–30y)

            If fitting fails, returns ``{"success": False, "error": ...}``.
        """
        if not ofz_data:
            return {"success": False, "error": "Empty OFZ data list"}

        # Sort by maturity for reproducibility
        sorted_data = sorted(ofz_data, key=lambda x: x.get("maturity_years", 0.0))

        maturities: list[float] = []
        ytms: list[float] = []
        for d in sorted_data:
            m = d.get("maturity_years")
            y = d.get("ytm")
            if m is not None and y is not None and m > 0:
                maturities.append(float(m))
                ytms.append(float(y))

        if len(maturities) < 4:
            return {
                "success": False,
                "error": f"Need at least 4 valid bonds, got {len(maturities)}",
            }

        # Compute weights:
        #   w = 1 / sqrt(maturity)
        # gives higher weight to short end (typical for curve fitting).
        weights = [1.0 / math.sqrt(max(m, 0.1)) for m in maturities]

        # First attempt: weighted fit
        result = YieldCurve.fit(maturities, ytms, weights=weights)

        # If weighted fit fails, try unweighted
        if not result.get("success", False):
            result = YieldCurve.fit(maturities, ytms, weights=None)

        # If still fails, return the error
        if not result.get("success", False):
            return result

        # ── Per-bond residuals ────────────────────────────────────────
        bond_results: list[dict[str, Any]] = []
        for i, d in enumerate(sorted_data):
            m = d.get("maturity_years", 0.0)
            y_obs = d.get("ytm", 0.0)
            y_fit = YieldCurve.get_rate(result, m) if m > 0 else 0.0
            bond_results.append({
                "maturity_years": float(m),
                "ytm_observed": float(y_obs),
                "ytm_fitted": round(float(y_fit), 6),
                "residual": round(float(y_obs) - float(y_fit), 6),
                "name": d.get("name", ""),
                "coupon": float(d.get("coupon", 0.0)),
                "price": float(d.get("price", 100.0)),
            })

        # ── Standard-tenor spot rates ─────────────────────────────────
        std_tenors = [0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 20, 25, 30]
        spot_rates = YieldCurve.get_spot_rates(result, std_tenors)

        result["bond_results"] = bond_results
        result["spot_rates"] = spot_rates
        result["data_points"] = len(ofz_data)

        return result
