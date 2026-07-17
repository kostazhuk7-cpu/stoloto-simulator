#!/usr/bin/env python3
"""
moex_bonds_analyzer — CLI-инструмент для анализа и рекомендаций по облигациям РФ.

Использование:
    python main.py --isin RU000A1090K0
    python main.py --portfolio bonds.csv
    python main.py --top 5
    python main.py --isin RU000A1090K0 --use-cbonds
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from bond_calculator import BondCalculator
from cache import MOEXCache
from config import Config
from data_fetcher import MOEXFetcher, CbondsFetcher
from recommender import Recommender

# Новые модули
from advanced_metrics import AdvancedMetrics
from scenario_analysis import ScenarioAnalyzer
from yield_curve import YieldCurve
from portfolio_optimizer import PortfolioOptimizer


def _setup_stdio() -> None:
    """Настройка stdout для корректного вывода Unicode/эмодзи на Windows."""
    if sys.platform == "win32":
        try:
            import io
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", errors="replace"
            )
        except Exception:
            pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="moex_bonds_analyzer — анализ и рекомендации по облигациям РФ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python main.py --isin RU000A1090K0\n"
            "  python main.py --portfolio bonds.csv\n"
            "  python main.py --top 5\n"
            "  python main.py --curve\n"
            "  python main.py --scenario RU000A1090K0\n"
            "  python main.py --scenario portfolio.csv\n"
            "  python main.py --optimize portfolio.csv"
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--isin", type=str, help="ISIN облигации для анализа")
    group.add_argument(
        "--portfolio", type=str, help="CSV-файл со списком ISIN (колонка isin)"
    )
    group.add_argument(
        "--top", type=int, metavar="N", help="Получить топ-N рекомендаций"
    )
    group.add_argument(
        "--curve",
        action="store_true",
        help="Построить кривую доходности ОФЗ (Nelson-Siegel-Svensson)",
    )
    group.add_argument(
        "--scenario",
        type=str,
        metavar="ISIN_OR_CSV",
        help="Сценарный анализ: ISIN облигации или CSV портфеля",
    )
    group.add_argument(
        "--optimize",
        type=str,
        metavar="CSV",
        help="Оптимизация портфеля (CSV с колонками isin,quantity)",
    )
    parser.add_argument(
        "--use-cbonds",
        action="store_true",
        help="Использовать Cbonds API для расширенной аналитики",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Путь к config.yaml (по умолчанию рядом с main.py)",
    )
    parser.add_argument(
        "--no-advanced",
        action="store_true",
        help="Не выводить расширенные метрики (convexity, DTS, KRD)",
    )
    return parser.parse_args(argv)


def print_bond_report(
    isin: str,
    bond_data: dict[str, Any],
    calc: dict[str, Any],
    recommendation: dict[str, Any],
) -> None:
    """Красивый вывод отчёта по одной облигации."""
    name = bond_data.get("shortname", "N/A")
    fullname = bond_data.get("fullname", "")

    # ── Заголовок ──
    print()
    print("=" * 60)
    print(f"  {name} ({fullname})")
    print(f"  ISIN: {isin}")
    print("=" * 60)

    # ── Ценовые параметры ──
    print(f"\n💰 Цена: {bond_data.get('current_price', 'N/A')}% "
          f"от номинала ({bond_data.get('price_value', 'N/A')} ₽)")
    print(f"📅 НКД: {bond_data.get('nkd', 'N/A')} ₽")
    print(f"🏦 До погашения: {bond_data.get('days_to_maturity', 'N/A')} дней")

    coupon_val = bond_data.get("coupon_value", 0)
    coupon_freq = bond_data.get("coupon_frequency", 0)
    if coupon_val and coupon_freq:
        print(f"💸 Купон: {coupon_val} ₽ × {coupon_freq} раз(а) в год "
              f"→ ставка {bond_data.get('coupon_percent', 'N/A')}%")

    if bond_data.get("offer_date"):
        print(f"📋 Оферта: {bond_data['offer_date']} "
              f"(через {bond_data.get('days_to_offer', 'N/A')} дн.)")

    # ── Метрики ──
    print(f"\n📈 Рассчитанные метрики "
          f"(без реинвестирования, налог {Config.load().tax_rate*100:.0f}%, "
          f"комиссия {Config.load().broker_commission*100:.2f}%):")

    cur_yield = calc.get("current_yield_pct", 0)
    print(f"  - Текущая купонная доходность: {cur_yield:.1f}%")

    simple = calc.get("simple_ytm", {})
    if simple and "error" not in simple:
        print(f"  - Простая годовая доходность к погашению: "
              f"{simple.get('annual_return_pct', 0):.1f}%")
        print(f"    • Цена: {simple.get('price_value', 0)} ₽")
        print(f"    • НКД: {simple.get('nkd', 0)} ₽")
        print(f"    • Комиссия: {simple.get('commission_purchase', 0):.2f} ₽")
        print(f"    • Расход всего: {simple.get('total_expense', 0):.2f} ₽")
        print(f"    • Доход до налогов: {simple.get('gross_income', 0):.2f} ₽")
        print(f"    • Налог 13%: {simple.get('tax', 0):.2f} ₽")
        print(f"    • Чистый доход: {simple.get('net_income', 0):.2f} ₽")

    eff = calc.get("effective_ytm", {})
    if eff and "error" not in eff:
        print(f"  - Эффективная YTM (численным методом): "
              f"{eff.get('ytm_pct', 0):.1f}%")
        print(f"  - Дюрация Маколея: {eff.get('macaulay_duration_years', 0):.2f} лет")
        print(f"  - Модифицированная дюрация: "
              f"{eff.get('modified_duration_years', 0):.2f} лет")

    eff_offer = calc.get("effective_ytm_offer")
    if eff_offer and "error" not in eff_offer:
        print(f"  - YTM до оферты: {eff_offer.get('ytm_pct', 0):.1f}%")

    # Доп. данные
    moex_ytm = bond_data.get("yield_moex")
    if moex_ytm is not None:
        print(f"  - Доходность (данные MOEX): {moex_ytm:.2f}%")
    zsp = bond_data.get("z_spread")
    if zsp is not None:
        print(f"  - Z-spread: {zsp:.2f}%")
    avg_vol = bond_data.get("avg_daily_volume", 0)
    print(f"  - Среднедневной объём: {avg_vol:,.0f} ₽")

    # ── Кредитный анализ (если есть Cbonds) ──
    # (пока просто уровень листинга)
    lvl = bond_data.get("list_level", "N/A")
    print(f"\n🏢 Кредитный анализ:")
    print(f"  - Уровень листинга MOEX: {lvl}")

    # ── Рекомендация ──
    signal = recommendation.get("signal", "N/A")
    score = recommendation.get("score", 0)

    if signal == "ПОКУПАТЬ":
        sign_str = "✅ ПОКУПАТЬ"
    elif signal == "ДЕРЖАТЬ":
        sign_str = "⚠️ ДЕРЖАТЬ"
    else:
        sign_str = "❌ ПРОДАВАТЬ"

    print(f"\n{sign_str}")
    print(f"  Балл: {score:.2f} / 1.00")
    print(f"\n  Обоснование:")
    for line in recommendation.get("justification", "").split("\n"):
        print(f"    {line}")

    # Риски
    risks = recommendation.get("risks", [])
    if risks:
        print(f"\n  ⚠️ Риски:")
        for r in risks:
            print(f"    • {r}")

    print()
    print("=" * 60)


def analyze_isin(
    isin: str,
    fetcher: MOEXFetcher,
    calculator: BondCalculator,
    recommender: Recommender,
    config: Config,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Полный анализ одной облигации."""
    bond_data = fetcher.get_bond_data(isin.upper())
    if not bond_data.get("isin_listed", False):
        raise ValueError(f"ISIN {isin} не найден или не торгуется на MOEX")

    calc_results = calculator.calculate_all(
        bond_data,
        tax_rate=config.tax_rate,
        commission_rate=config.broker_commission,
    )
    recommendation = recommender.rate(bond_data, calc_results)
    return bond_data, calc_results, recommendation


def analyze_portfolio(
    csv_path: str,
    fetcher: MOEXFetcher,
    calculator: BondCalculator,
    recommender: Recommender,
    config: Config,
) -> list[tuple[str, dict, dict, dict, Optional[str]]]:
    """Анализ портфеля из CSV."""
    results: list[tuple[str, dict, dict, dict, Optional[str]]] = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if "isin" not in (reader.fieldnames or []):
            raise ValueError("CSV должен содержать колонку 'isin'")
        for row in reader:
            isin = row["isin"].strip()
            if not isin:
                continue
            try:
                bd, cr, rec = analyze_isin(isin, fetcher, calculator, recommender, config)
                results.append((isin, bd, cr, rec, None))
            except ValueError as e:
                results.append((isin, {}, {}, {}, str(e)))
    return results


def print_bond_report_extended(
    isin: str,
    bond_data: dict[str, Any],
    calc: dict[str, Any],
    recommendation: dict[str, Any],
) -> None:
    """Расширенный отчёт с convexity, DTS, KRD."""
    print_bond_report(isin, bond_data, calc, recommendation)

    # Convexity
    conv = calc.get("convexity", 0)
    mod_conv = calc.get("modified_convexity", 0)
    if conv:
        print(f"\n  - Выпуклость: {conv:.2f}")
        print(f"  - Модифицированная выпуклость: {mod_conv:.2f}")

    # DTS
    mod_dur = calc.get("modified_duration_years", 0)
    z_spread = bond_data.get("z_spread", 0.0)
    if mod_dur > 0 and z_spread > 0:
        dts_val = mod_dur * z_spread
        print(f"  - DTS (Duration×Spread): {dts_val:.2f} лет·%")

    # KRD
    print()
    print("  Key Rate Durations (конечно-разностные):")
    try:
        krd = AdvancedMetrics.calculate_key_rate_durations(
            face_value=bond_data.get("face_value", 1000),
            coupon_value=bond_data.get("coupon_value", 0),
            coupon_frequency=bond_data.get("coupon_frequency", 4),
            ytm_decimal=calc.get("effective_ytm", {}).get("ytm_pct", 0) / 100.0,
            years_to_maturity=bond_data.get("days_to_maturity", 0) / 365.0,
            price_value=bond_data.get("price_value", 0),
        )
        for tenor, krd_val in krd.items():
            print(f"    • {tenor}: {krd_val:.4f}")
    except Exception:
        print("    (недостаточно данных)")


def run_scenario_analysis(
    target: str,
    fetcher: MOEXFetcher,
    calculator: BondCalculator,
    config: Config,
) -> None:
    """Сценарный анализ для облигации или портфеля."""
    target_path = Path(target)
    if target_path.exists():
        # Портфель из CSV
        from data_fetcher import MOEXFetcher as _MF
        from cache import MOEXCache as _MCache
        cache = _MCache()
        fetcher_local = _MF(config, cache)
        calc_local = BondCalculator()

        with open(target, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if "isin" not in (reader.fieldnames or []):
                raise ValueError("CSV должен содержать колонку 'isin'")
            portfolio: list[dict[str, Any]] = []
            for row in reader:
                isin = row["isin"].strip()
                if not isin:
                    continue
                try:
                    bd = fetcher_local.get_bond_data(isin)
                    cr = calc_local.calculate_all(
                        bd,
                        tax_rate=config.tax_rate,
                        commission_rate=config.broker_commission,
                    )
                    qty = float(row.get("quantity", row.get("amount", 1)))
                    portfolio.append({
                        "bond_data": bd,
                        "calc_results": cr,
                        "quantity": qty,
                    })
                except Exception as e:
                    print(f"  ⚠ {isin}: {e}")
            cache.close()
            fetcher_local.close()

        if not portfolio:
            print("❌ Нет данных для анализа")
            return

        results = ScenarioAnalyzer.analyze_portfolio(portfolio, None)
        print()
        print(ScenarioAnalyzer.format_scenario_table(results, target))

        prob = ScenarioAnalyzer.probability_weighted_return(results, None)
        print(f"\nВероятностно-взвешенная доходность:")
        print(f"  Ожидаемая доходность: {prob['expected_return_pct']:.2f}%")
        print(f"  Стандартное отклонение: {prob['std_dev_pct']:.2f}%")
        print(f"  Коэффициент Шарпа: {prob['sharpe_ratio_estimate']:.2f}")
    else:
        # Одиночная облигация по ISIN
        from data_fetcher import MOEXFetcher as _MF
        from cache import MOEXCache as _MCache
        cache = _MCache()
        fetcher_local = _MF(config, cache)
        calc_local = BondCalculator()

        isin = target.upper()
        bd = fetcher_local.get_bond_data(isin)
        cr = calc_local.calculate_all(
            bd,
            tax_rate=config.tax_rate,
            commission_rate=config.broker_commission,
        )
        cache.close()
        fetcher_local.close()

        results = ScenarioAnalyzer.analyze_bond(bd, cr, None)
        name = bd.get("shortname", isin)
        print()
        print(ScenarioAnalyzer.format_scenario_table(results, name))

        prob = ScenarioAnalyzer.probability_weighted_return(results, None)
        print(f"\nВероятностно-взвешенная доходность:")
        print(f"  Ожидаемая доходность: {prob['expected_return_pct']:.2f}%")
        print(f"  Стандартное отклонение: {prob['std_dev_pct']:.2f}%")
        print(f"  Коэффициент Шарпа: {prob['sharpe_ratio_estimate']:.2f}")


def run_yield_curve(
    fetcher: MOEXFetcher,
    config: Config,
) -> None:
    """Построить кривую доходности ОФЗ через NSS."""
    print("\n=== Построение кривой доходности ОФЗ ===")
    print("Загружаем данные ОФЗ с MOEX...")

    # --- 1. Получаем список ОФЗ с доски TQOB ---
    # TQOB — основная доска для ОФЗ, в marketdata есть поле YIELD.
    top = fetcher.get_board_bonds("TQOB", limit=50)

    # Если TQOB пустая, пробуем TQCB (некоторые ОФЗ могут быть там)
    if not top:
        top = fetcher.get_board_bonds("TQCB", limit=50)

    ofz_data: list[dict[str, Any]] = []

    for bond_entry in top:
        secid = bond_entry.get("SECID", "")
        if not secid or not secid.startswith("SU"):
            continue  # Только ОФЗ (SU...)
        try:
            desc = fetcher.get_description(secid)
            if not desc:
                continue
            isin = desc.get("ISIN", "")
            name = desc.get("SHORTNAME", "")
            if not isin:
                continue
            if "ОФЗ" not in name and "ОФЗ" not in desc.get("NAME", ""):
                continue

            # YIELD из marketdata (TQOB — корректный)
            yld_raw = bond_entry.get("YIELD")
            ytm = float(yld_raw) if yld_raw not in (None, "null", "") else None
            if ytm is None or ytm <= 0.0:
                continue

            # Цена: LAST → PREVPRICE → 100
            price_raw = bond_entry.get("LAST") or bond_entry.get("PREVPRICE")
            price = float(price_raw) if price_raw not in (None, "null", "") else 100.0

            # Срок до погашения из description
            matdate = desc.get("MATDATE", "")
            days = 0
            if matdate:
                try:
                    md = datetime.strptime(matdate, "%Y-%m-%d").date()
                    days = (md - date.today()).days
                    if days < 0:
                        days = 0
                except ValueError:
                    pass
            if days <= 0:
                continue

            # Купон из description
            coupon_pct = 0.0
            try:
                cp = desc.get("COUPONPERCENT", "0")
                coupon_pct = float(cp) if cp not in (None, "null", "") else 0.0
            except (ValueError, TypeError):
                pass

            ofz_data.append({
                "maturity_years": days / 365.0,
                "ytm": ytm / 100.0,  # YTM из TQOB в десятичный вид
                "coupon": coupon_pct / 100.0,
                "price": price,
                "name": name,
            })
        except Exception:
            continue

    if len(ofz_data) < 4:
        print(f"❌ Недостаточно ОФЗ: найдено {len(ofz_data)}, нужно ≥4")
        return

    print(f"Найдено ОФЗ: {len(ofz_data)}")
    result = YieldCurve.build_from_ofz(ofz_data)

    if not result.get("success", False):
        print(f"❌ Ошибка подгонки: {result.get('error', 'неизвестная')}")
        return

    print(f"\nПараметры NSS:")
    print(f"  β₀ (долгосрочный уровень): {result['beta0']:.6f}")
    print(f"  β₁ (наклон): {result['beta1']:.6f}")
    print(f"  β₂ (средняя кривизна): {result['beta2']:.6f}")
    print(f"  β₃ (долгосрочная кривизна): {result['beta3']:.6f}")
    print(f"  τ₁: {result['tau1']:.4f}")
    print(f"  τ₂: {result['tau2']:.4f}")
    print(f"\nКачество подгонки:")
    print(f"  R²: {result.get('r_squared', 0):.4f}")
    print(f"  RMSE: {result.get('rmse', 0):.4f}")

    # Спот-ставки
    spot = result.get("spot_rates", {})
    if spot:
        print(f"\nСпот-ставки (ключевые сроки):")
        for tenor, rate in spot.items():
            print(f"  {tenor:>4}: {rate*100:.2f}%")

    # Таблица residuals
    bond_res = result.get("bond_results", [])
    if bond_res:
        print(f"\nПооблигационные остатки (первые 15):")
        print(f"  {'Облигация':<25} {'Срок, лет':>10} {'YTM набл':>10} "
              f"{'YTM модель':>10} {'Остаток':>10}")
        for br in bond_res[:15]:
            bname = br.get("name", br.get("coupon", "N/A"))
            name_str = str(bname) if len(str(bname)) <= 24 else str(bname)[:21] + "..."
            print(f"  {name_str:<25} {br['maturity_years']:>10.2f} "
                  f"{br['ytm_observed']*100:>9.2f}% "
                  f"{br['ytm_fitted']*100:>9.2f}% "
                  f"{br['residual']*100:>+9.2f}%")


def run_portfolio_optimization(
    csv_path: str,
    fetcher: MOEXFetcher,
    calculator: BondCalculator,
    config: Config,
) -> None:
    """Оптимизация портфеля облигаций."""
    print(f"\n=== Оптимизация портфеля: {csv_path} ===")

    from data_fetcher import MOEXFetcher as _MF
    from cache import MOEXCache as _MCache
    cache = _MCache()
    fetcher_local = _MF(config, cache)
    calc_local = BondCalculator()

    bonds: list[dict[str, Any]] = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if "isin" not in (reader.fieldnames or []):
            raise ValueError("CSV должен содержать колонку 'isin'")
        for row in reader:
            isin = row["isin"].strip()
            if not isin:
                continue
            try:
                bd = fetcher_local.get_bond_data(isin)
                cr = calc_local.calculate_all(
                    bd,
                    tax_rate=config.tax_rate,
                    commission_rate=config.broker_commission,
                )
                eff = cr.get("effective_ytm", {})
                bonds.append({
                    "name": bd.get("shortname", isin),
                    "isin": isin,
                    "ytm": eff.get("ytm_pct", 0),
                    "modified_duration": cr.get("modified_duration_years", 0),
                    "z_spread": bd.get("z_spread", 1.0),
                    "convexity": cr.get("convexity", 0),
                    "weight": 1.0,
                })
            except Exception as e:
                print(f"  ⚠ {isin}: {e}")

    cache.close()
    fetcher_local.close()

    if not bonds:
        print("❌ Нет данных для оптимизации")
        return

    print(f"\nЗагружено облигаций: {len(bonds)}")

    # Risk Parity
    print(f"\n--- Risk Parity ---")
    rp = PortfolioOptimizer.risk_parity(bonds)
    print("  Веса:")
    for name, w in sorted(rp["weights"].items(), key=lambda x: -x[1]):
        print(f"    {name:<25} {w*100:.2f}%")
    print(f"  Дюрация: {rp['duration_target']:.2f} лет")
    print(f"  DTS: {rp['dts_target']:.4f}")

    # Mean-Variance
    print(f"\n--- Mean-Variance (γ=1.0) ---")
    mv = PortfolioOptimizer.mean_variance(bonds, risk_aversion=1.0)
    print("  Веса:")
    for name, w in sorted(mv["weights"].items(), key=lambda x: -x[1]):
        print(f"    {name:<25} {w*100:.2f}%")
    print(f"  Ожидаемая доходность: {mv['expected_return']:.2f}%")
    print(f"  Риск портфеля: {mv['portfolio_risk']:.2f}%")
    print(f"  Коэффициент Шарпа: {mv['sharpe_ratio']:.2f}")

    # Efficiency Report
    for b in bonds:
        b["weight"] = mv["weights"].get(b["name"], 0)
    er = PortfolioOptimizer.efficiency_report(bonds)
    print(f"\n--- Эффективность портфеля ---")
    print(f"  YTM: {er['portfolio_ytm']:.2f}%")
    print(f"  Дюрация: {er['portfolio_duration']:.2f} лет")
    print(f"  Выпуклость: {er['portfolio_convexity']:.2f}")
    print(f"  DTS: {er['portfolio_dts']:.4f}")
    print(f"  Эффективное число бумаг: {er['effective_num_bonds']:.1f}")


def main() -> None:
    _setup_stdio()
    args = parse_args()
    config_path = Path(args.config) if args.config else None
    config = Config.load(config_path)

    # Загружаем ключевую ставку ЦБ
    cache = MOEXCache()
    fetcher = MOEXFetcher(config, cache)
    if config.risk_free_rate is None:
        try:
            config.risk_free_rate = fetcher.get_cbr_key_rate()
        except Exception as e:
            print(f"⚠️ Не удалось получить ключевую ставку ЦБ: {e}")
            print(f"   Использую значение по умолчанию: 14.5%")
            config.risk_free_rate = 0.145  # fallback

    calculator = BondCalculator()
    recommender_instance = Recommender(config)

    def _report(isin, bd, cr, rec):
        """Выбрать формат отчёта в зависимости от --no-advanced."""
        if args.no_advanced:
            print_bond_report(isin, bd, cr, rec)
        else:
            print_bond_report_extended(isin, bd, cr, rec)

    try:
        if args.isin:
            isin = args.isin.upper()
            bond_data, calc_results, recommendation = analyze_isin(
                isin, fetcher, calculator, recommender_instance, config
            )
            _report(isin, bond_data, calc_results, recommendation)

        elif args.portfolio:
            results = analyze_portfolio(
                args.portfolio, fetcher, calculator, recommender_instance, config
            )
            print(f"\n{'='*60}")
            print(f"  Анализ портфеля: {args.portfolio}")
            print(f"{'='*60}")
            for isin, bd, cr, rec, err in results:
                if err:
                    print(f"\n❌ {isin}: {err}")
                else:
                    _report(isin, bd, cr, rec)
                    print(f"\n  {'─'*50}")

        elif args.curve:
            run_yield_curve(fetcher, config)

        elif args.scenario:
            run_scenario_analysis(args.scenario, fetcher, calculator, config)

        elif args.optimize:
            run_portfolio_optimization(args.optimize, fetcher, calculator, config)

        elif args.top:
            print(f"\n{'='*60}")
            print(f"  Топ-{args.top} рекомендаций")
            print(f"{'='*60}")

            # Используем batch-запрос get_board_bonds_full (содержит ISIN)
            # вместо N индивидуальных get_description — в 2N+1 раз меньше API-вызовов
            bonds_full = fetcher.get_board_bonds_full("TQCB", limit=args.top * 3)
            scored: list[tuple[float, str, dict, dict, dict]] = []

            for i, bond_entry in enumerate(bonds_full):
                isin = bond_entry.get("ISIN", "")
                if not isin:
                    continue
                try:
                    bd, cr, rec = analyze_isin(
                        isin, fetcher, calculator, recommender_instance, config
                    )
                    score = rec.get("score", 0)
                    scored.append((score, isin, bd, cr, rec))

                except (ValueError, Exception):
                    continue

                if len(scored) >= args.top * 2:
                    break

            scored.sort(key=lambda x: x[0], reverse=True)

            for rank, (score, isin, bd, cr, rec) in enumerate(scored[:args.top], 1):
                print(f"\n  #{rank} — {bd.get('shortname', isin)} "
                      f"(ISIN: {isin})")
                print(f"  Балл: {score:.2f} | "
                      f"Сигнал: {rec.get('signal', 'N/A')} | "
                      f"Доходность: {cr.get('simple_ytm', {}).get('annual_return_pct', 0):.1f}%")
                print(f"  {'─'*40}")

    except ValueError as e:
        print(f"❌ Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ Непредвиденная ошибка: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        cache.close()
        fetcher.close()


if __name__ == "__main__":
    main()
