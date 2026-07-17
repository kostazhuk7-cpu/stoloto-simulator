"""
Advanced bond analytics: convexity, DTS, key rate duration, carry-roll-down.

All methods are static — pure computation, no state.
Follows the conservative conventions of the rest of the codebase.
"""
from __future__ import annotations

import math
from typing import Any, Optional


class AdvancedMetrics:
    """Advanced bond risk and return metrics."""

    # Standard key rate tenors for Russian market (years)
    KEY_RATE_TENORS: list[float] = [0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30]

    # ------------------------------------------------------------------
    #  Convexity
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_convexity(
        *,
        face_value: float,
        coupon_value: float,
        coupon_frequency: int,
        ytm_decimal: float,
        years_to_maturity: float,
        price_value: float,
    ) -> float:
        """
        Calculate annualised convexity for a bullet bond.

        Convexity = (1 / P) * d²P / dy²

        Formula (annual compounding, annualised):
            C = [ Σ(t * (t + 1/tau) * CF_t / (1 + y/τ)^(t + 2)) ] / P

        where τ = coupon_frequency, y = YTM (decimal), t = period number,
        CF_t = cash flow at period t.

        Returns convexity in years² (annualised).
        """
        if years_to_maturity <= 0 or price_value <= 0:
            return 0.0

        periods_per_year = max(coupon_frequency, 1)
        num_periods = int(years_to_maturity * periods_per_year)
        if num_periods < 1:
            num_periods = 1

        period_coupon = coupon_value
        period_rate = ytm_decimal / periods_per_year
        one_plus_r = 1.0 + period_rate

        # Sum: Σ t * (t + 1) * CF_t / (1 + r)^(t + 2)
        sum_convexity = 0.0

        for t in range(1, num_periods + 1):
            cf = period_coupon
            pv_factor = cf / (one_plus_r ** (t + 2))
            sum_convexity += t * (t + 1) * pv_factor

        # Add principal at maturity
        pv_principal = face_value / (one_plus_r ** (num_periods + 2))
        sum_convexity += num_periods * (num_periods + 1) * pv_principal

        # Convexity = (1 / (periods_per_year² * price)) * sum
        convexity = sum_convexity / (periods_per_year ** 2 * price_value)

        return convexity

    @staticmethod
    def approximate_convexity(macaulay_duration_years: float, ytm_decimal: float) -> float:
        """
        Approximate convexity from Macaulay duration and YTM.

        C ≈ (D² + D) / (1 + y)²

        Useful when full cash-flow data is unavailable.
        """
        if macaulay_duration_years <= 0:
            return 0.0
        if ytm_decimal < 0:
            ytm_decimal = 0.0
        return (macaulay_duration_years ** 2 + macaulay_duration_years) / \
               ((1.0 + ytm_decimal) ** 2)

    # ------------------------------------------------------------------
    #  DTS — Duration Times Spread
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_dts(
        modified_duration_years: float,
        z_spread_decimal: float,
    ) -> float:
        """
        Duration Times Spread — the primary risk measure for corporate bonds.

        DTS = Modified Duration × Z-spread (in decimal)

        A bond with DUR=5 and Z-spread=2% has DTS = 5 × 0.02 = 0.10.
        Portfolio DTS is sum(w_i × DTS_i).
        """
        return modified_duration_years * z_spread_decimal

    # ------------------------------------------------------------------
    #  Key Rate Durations
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_key_rate_durations(
        *,
        face_value: float,
        coupon_value: float,
        coupon_frequency: int,
        ytm_decimal: float,
        years_to_maturity: float,
        price_value: float,
        tenors: Optional[list[float]] = None,
        shift_bps: float = 100.0,
    ) -> dict[str, float]:
        """
        Calculate key rate durations using finite-difference perturbation.

        For each key rate tenor t:
            KRD_t = -(P(+shift) - P(-shift)) / (2 * shift * P(0))

        where the zero-coupon rate at maturity = t is shifted by ±shift_bps
        and all other rates are linearly interpolated.

        Parameters
        ----------
        tenors : list[float], optional
            Key rate tenors in years. Defaults to KEY_RATE_TENORS.
        shift_bps : float
            Shift size in basis points (default 100 = 1%).

        Returns
        -------
        dict[str, float]
            {f"{tenor}y": krd_value} for each key rate tenor.
        """
        if tenors is None:
            tenors = AdvancedMetrics.KEY_RATE_TENORS

        if price_value <= 0 or years_to_maturity <= 0:
            return {f"{t:.2f}y": 0.0 for t in tenors}

        shift = shift_bps / 10000.0  # convert bps to decimal

        # Build cash flows
        periods_per_year = max(coupon_frequency, 1)
        num_periods = int(years_to_maturity * periods_per_year)
        if num_periods < 1:
            num_periods = 1

        cash_flows: list[tuple[float, float]] = []
        for t in range(1, num_periods):
            time_years = t / periods_per_year
            cash_flows.append((time_years, coupon_value))
        # Final payment: coupon + face
        time_years = num_periods / periods_per_year
        cash_flows.append((time_years, coupon_value + face_value))

        def _price_at_shifted_curve(
            shift_vector: dict[float, float],
        ) -> float:
            """Price bond given a shift at each key rate tenor."""
            pv = 0.0
            for t_years, cf in cash_flows:
                # Interpolate shift for this cash flow's maturity
                s = AdvancedMetrics._interpolate_shift(
                    t_years, tenors, shift_vector
                )
                # Discount: ytm + interpolated shift
                rate = ytm_decimal + s
                # Convert to periodic rate
                period_rate = rate / periods_per_year
                pv += cf / ((1.0 + period_rate) ** (t_years * periods_per_year))
            return pv

        base_price = _price_at_shifted_curve({t: 0.0 for t in tenors})

        krd: dict[str, float] = {}
        for tenor in tenors:
            shift_up: dict[float, float] = {}
            shift_down: dict[float, float] = {}
            for t in tenors:
                if abs(t - tenor) < 1e-10:
                    shift_up[t] = shift
                    shift_down[t] = -shift
                else:
                    shift_up[t] = 0.0
                    shift_down[t] = 0.0

            price_up = _price_at_shifted_curve(shift_up)
            price_down = _price_at_shifted_curve(shift_down)

            # KRD = -(P_up - P_down) / (2 * shift * P0)
            if abs(2.0 * shift * base_price) > 1e-15:
                krd_val = -(price_up - price_down) / (2.0 * shift * base_price)
            else:
                krd_val = 0.0

            label = f"{tenor:.2f}y" if tenor < 1 else f"{int(tenor)}y"
            krd[label] = round(krd_val, 6)

        return krd

    @staticmethod
    def _interpolate_shift(
        time_years: float,
        tenors: list[float],
        shift_vector: dict[float, float],
    ) -> float:
        """
        Linearly interpolate the rate shift at a given maturity.

        For maturities beyond the longest tenor, use the longest tenor's shift.
        For maturities before the shortest tenor, use the shortest tenor's shift.
        """
        if time_years <= tenors[0]:
            return shift_vector.get(tenors[0], 0.0)
        if time_years >= tenors[-1]:
            return shift_vector.get(tenors[-1], 0.0)

        for i in range(len(tenors) - 1):
            if tenors[i] <= time_years <= tenors[i + 1]:
                t0, t1 = tenors[i], tenors[i + 1]
                s0 = shift_vector.get(t0, 0.0)
                s1 = shift_vector.get(t1, 0.0)
                # Linear interpolation
                w = (time_years - t0) / (t1 - t0) if (t1 - t0) > 0 else 0.0
                return s0 + w * (s1 - s0)

        return 0.0

    # ------------------------------------------------------------------
    #  Carry-Roll-Down
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_carry_roll_down(
        *,
        face_value: float,
        price_value: float,
        nkd: float,
        coupon_value: float,
        coupon_frequency: int,
        ytm_decimal: float,
        years_to_maturity: float,
        horizon_days: int = 90,
        curve_slope_bps: float = 0.0,
        tax_rate: float = 0.13,
        commission_rate: float = 0.0004,
    ) -> dict[str, Any]:
        """
        Calculate carry-roll-down return over a given horizon.

        Components:
            *carry*         — income from coupon accrual + coupon payments
            *roll_down*     — price change from riding the yield curve
            *total_return*  — carry + roll_down (before tax & commission)

        When ``curve_slope_bps`` is provided, the roll-down assumes the
        bond slides down a parallel yield curve by (horizon_years × slope).
        A positive slope means longer yields are higher, so rolling down
        the curve increases price (bullish for bond holders).

        Parameters
        ----------
        curve_slope_bps : float
            Slope of the yield curve (bps/year).  Positive = upward-sloping.
            E.g., 50 means the curve rises 50 bps per year of maturity.
        horizon_days : int
            Investment horizon in days (default 90 = ~3 months).

        Returns
        -------
        dict with keys:
            horizon_days, carry_pct, roll_down_pct, total_return_pct,
            annualised_return_pct, new_price, new_ytm_decimal.
        """
        horizon_years = horizon_days / 365.0
        if years_to_maturity <= 0:
            return {
                "horizon_days": horizon_days,
                "carry_pct": 0.0,
                "roll_down_pct": 0.0,
                "total_return_pct": 0.0,
                "annualised_return_pct": 0.0,
                "new_price": 0.0,
                "new_ytm_decimal": 0.0,
            }

        # --- Carry: coupon income over horizon ---
        periods_per_year = max(coupon_frequency, 1)
        coupon_interval_years = 1.0 / periods_per_year
        coupons_in_horizon = horizon_years / coupon_interval_years

        # Coupon payments received within horizon (whole coupons)
        full_coupons = int(coupons_in_horizon)
        # Accrued interest on final partial period
        partial_fraction = coupons_in_horizon - full_coupons

        gross_carry = full_coupons * coupon_value + partial_fraction * (
            coupon_value * coupon_interval_years * periods_per_year
        )
        # Tax on coupon income
        carry_after_tax = gross_carry * (1.0 - tax_rate)
        carry_pct = (carry_after_tax / price_value) * 100.0 if price_value > 0 else 0.0

        # --- Roll-down: price change from yield change ---
        # Bond slides down the curve: remaining maturity decreases by horizon_years
        # Yield changes by: -(curve_slope_bps / 10000) * horizon_years
        dy = -(curve_slope_bps / 10000.0) * horizon_years

        new_remaining_years = max(0.01, years_to_maturity - horizon_years)
        new_ytm = ytm_decimal + dy  # lower YTM if curve slopes up

        # Price the bond at new YTM
        new_price_value = AdvancedMetrics._price_bond(
            face_value=face_value,
            coupon_value=coupon_value,
            coupon_frequency=coupon_frequency,
            ytm_decimal=new_ytm,
            years_to_maturity=new_remaining_years,
        )

        # Commission on sale
        comm_sale = new_price_value * commission_rate

        roll_down_pct = ((new_price_value - price_value - comm_sale) / price_value) * 100.0

        total_return_pct = carry_pct + roll_down_pct
        annualised_return_pct = total_return_pct / horizon_years if horizon_years > 0 else 0.0
        new_price_pct = (new_price_value / face_value) * 100.0 if face_value > 0 else 0.0

        return {
            "horizon_days": horizon_days,
            "coupons_received": full_coupons,
            "carry_pct": round(carry_pct, 4),
            "roll_down_pct": round(roll_down_pct, 4),
            "total_return_pct": round(total_return_pct, 4),
            "annualised_return_pct": round(annualised_return_pct, 4),
            "new_price": round(new_price_pct, 2),
            "new_ytm_decimal": round(new_ytm, 6),
            "curve_slope_bps": curve_slope_bps,
        }

    @staticmethod
    def _price_bond(
        *,
        face_value: float,
        coupon_value: float,
        coupon_frequency: int,
        ytm_decimal: float,
        years_to_maturity: float,
    ) -> float:
        """Price a bullet bond given its YTM (dirty price without accrued)."""
        if years_to_maturity <= 0:
            return face_value

        periods_per_year = max(coupon_frequency, 1)
        num_periods = int(years_to_maturity * periods_per_year)
        if num_periods < 1:
            num_periods = 1

        period_rate = ytm_decimal / periods_per_year
        one_plus_r = 1.0 + period_rate

        # PV of coupons
        pv_coupons = 0.0
        if abs(period_rate) > 1e-15:
            pv_coupons = coupon_value * (1.0 - 1.0 / (one_plus_r ** num_periods)) / period_rate
        else:
            pv_coupons = coupon_value * num_periods

        # PV of principal
        pv_principal = face_value / (one_plus_r ** num_periods)

        return pv_coupons + pv_principal
