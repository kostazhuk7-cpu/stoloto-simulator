#!/usr/bin/env python3
"""
moex_bonds_analyzer — Streamlit веб-интерфейс.

Запуск:
    streamlit run app.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

# Настройка stdout для Windows (если запущен из cmd/powershell)
if sys.platform == "win32":
    try:
        sys.stdout = __import__("io").TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace"
        )
    except Exception:
        pass

from config import Config, TInvestConfig
from cache import MOEXCache
from data_fetcher import MOEXFetcher
from bond_calculator import BondCalculator
from recommender import Recommender
from advanced_metrics import AdvancedMetrics
from scenario_analysis import ScenarioAnalyzer
from yield_curve import YieldCurve
from portfolio_optimizer import PortfolioOptimizer
from tinvest_client import TInvestClient, _money_to_float, _quotation_to_float


# ── Состояние сессии ──────────────────────────────────────────────────────────

def _init_session() -> None:
    """Инициализация / кэширование тяжёлых объектов в session_state."""
    if "config" not in st.session_state:
        st.session_state.config = Config.load()
    if "cache" not in st.session_state:
        st.session_state.cache = MOEXCache()
    if "fetcher" not in st.session_state:
        st.session_state.fetcher = MOEXFetcher(st.session_state.config, st.session_state.cache)
    if "calculator" not in st.session_state:
        st.session_state.calculator = BondCalculator()
    if "recommender" not in st.session_state:
        st.session_state.recommender = Recommender(st.session_state.config)
    if "tinvest" not in st.session_state:
        cfg = st.session_state.config
        st.session_state.tinvest = TInvestClient(
            TInvestConfig(token=cfg.tinvest.token, sandbox=cfg.tinvest.sandbox),
            verify_ssl=False,
        )
    # Авто-загрузка ключевой ставки — всегда свежая с cbr.ru
    if st.session_state.config.risk_free_rate is None:
        try:
            st.session_state.config.risk_free_rate = st.session_state.fetcher.get_cbr_key_rate()
        except Exception as e:
            st.warning(f"⚠️ Не удалось получить ключевую ставку ЦБ: {e}")
            st.warning(f"   Использую значение по умолчанию: 14.5%")
            st.session_state.config.risk_free_rate = 0.145  # fallback


def _fetcher() -> MOEXFetcher:
    return st.session_state.fetcher


def _calc() -> BondCalculator:
    return st.session_state.calculator


def _rec() -> Recommender:
    return st.session_state.recommender


def _cfg() -> Config:
    return st.session_state.config


def _tinvest() -> TInvestClient:
    return st.session_state.tinvest


# ── Вспомогательные функции отображения ───────────────────────────────────────

def _signal_emoji(signal: str) -> str:
    if signal == "ПОКУПАТЬ":
        return "green"
    if signal == "ДЕРЖАТЬ":
        return "orange"
    return "red"


def _n(val: Any, default: Any = 0) -> Any:
    """Be safe against None: return default when val is None."""
    return default if val is None else val


def _metric_card(container, label: str, value: str, color: str,
                 help_text: str = "") -> None:
    """Отрисовать метрику с цветным значением в container (st.column).
    help_text отображается как HTML title (тултип при наведении)."""
    color_map = {"green": "#00cc66", "red": "#ff4444", "orange": "#ffaa00", "white": "#ffffff"}
    c = color_map.get(color, color)
    title_attr = f' title="{help_text}"' if help_text else ""
    container.markdown(
        f'<div style="padding:0.5rem 0; line-height:1.3"{title_attr}>'
        f'<div style="font-size:0.8rem; color:#888; margin-bottom:2px">{label}</div>'
        f'<div style="font-size:1.5rem; font-weight:700; color:{c}">{value}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_metric_cards(bd: dict, cr: dict) -> None:
    """Карточки KPI в верхней части экрана."""
    col1, col2, col3, col4 = st.columns(4)
    price = _n(bd.get("current_price"))
    ytm_moex = _n(bd.get("yield_moex"))
    eff_ytm = _n(cr.get("effective_ytm", {}).get("ytm_pct"))
    if eff_ytm <= 0:
        eff_ytm = ytm_moex
    mod_dur = _n(cr.get("modified_duration_years"))

    col1.metric("Цена", f"{price:.2f}%", delta=None,
                help="Текущая рыночная цена в процентах от номинала. 100% = номинал")
    col2.metric("YTM (MOEX)", f"{ytm_moex:.2f}%",
                help="Доходность к погашению по данным MOEX. Учитывает все купоны + разницу покупки/погашения")
    col3.metric("Эффект. YTM", f"{eff_ytm:.2f}%",
                help="Эффективная доходность с учётом комиссии брокера и НДФЛ. Реальная доходность «на руки»")
    col4.metric("Мод. дюрация", f"{mod_dur:.2f} лет",
                help="Модифицированная дюрация — чувствительность цены к изменению ставки на 1%. Чем выше — тем больше риск при росте ставок")


def _render_basic_info(bd: dict) -> None:
    """Основная информация об облигации в two-column layout."""
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**Название:** {bd.get('shortname', '—')}")
        st.markdown(f"**ISIN:** `{bd.get('isin', '—')}`")
        st.markdown(f"**SECID:** `{bd.get('secid', '—')}`")
        st.markdown(f"**Номинал:** {_n(bd.get('face_value'), 1000):.0f} ₽")
    with c2:
        st.markdown(f"**Купон:** {_n(bd.get('coupon_value')):.2f} ₽ × "
                     f"{_n(bd.get('coupon_frequency'), 4)} раз/год")
        st.markdown(f"**Ставка:** {_n(bd.get('coupon_percent')):.2f}%")
        st.markdown(f"**НКД:** {_n(bd.get('nkd')):.2f} ₽")
        st.markdown(f"**Дней до погашения:** {bd.get('days_to_maturity') or '—'}")


def _render_extended_metrics(bd: dict, cr: dict) -> None:
    """Расширенные метрики: выпуклость, DTS, KRD."""
    st.subheader("Расширенные метрики")
    st.caption("Продвинутые показатели для оценки рисков и доходности облигации.")

    conv = _n(cr.get("convexity"))
    mod_conv = _n(cr.get("modified_convexity"))
    mod_dur = _n(cr.get("modified_duration_years"))
    z_spread = _n(bd.get("z_spread"))
    dts = mod_dur * z_spread if mod_dur > 0 and z_spread > 0 else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("Выпуклость", f"{conv:.2f}",
              help="Вогнутость цены относительно доходности. Положительная = при росте ставок цена падает медленнее, при падении — растёт быстрее")
    c2.metric("Мод. выпуклость", f"{mod_conv:.2f}",
              help="Модифицированная выпуклость — вторая производная цены. Уточняет дюрацию при больших изменениях ставки")
    c3.metric("DTS (Dur × Spread)", f"{dts:.2f} лет·%",
              help="Duration Times Spread: дюрация × кредитный спред. Показывает чувствительность к ухудшению кредитного качества эмитента")

    # KRD
    with st.expander("Key Rate Durations (конечно-разностные)", expanded=False):
        try:
            ytm_dec = _n(cr.get("effective_ytm", {}).get("ytm_pct")) / 100.0
            krd = AdvancedMetrics.calculate_key_rate_durations(
                face_value=_n(bd.get("face_value"), 1000),
                coupon_value=_n(bd.get("coupon_value")),
                coupon_frequency=_n(bd.get("coupon_frequency"), 4),
                ytm_decimal=ytm_dec,
                years_to_maturity=_n(bd.get("days_to_maturity")) / 365.0,
                price_value=_n(bd.get("price_value")),
            )
            krd_df = pd.DataFrame(
                list(krd.items()), columns=["Тенор", "KRD"]
            )
            st.dataframe(krd_df, use_container_width=True, hide_index=True)
        except Exception as e:
            st.warning(f"Недостаточно данных для KRD: {e}")


def _render_recommendation(rec: dict) -> None:
    """Блок с рекомендацией."""
    signal = rec.get("signal") or "N/A"
    score = _n(rec.get("score"))

    if signal == "ПОКУПАТЬ":
        label = "✅ ПОКУПАТЬ"
    elif signal == "ДЕРЖАТЬ":
        label = "⚠️ ДЕРЖАТЬ"
    else:
        label = "❌ ПРОДАВАТЬ"

    st.subheader("Рекомендация")
    c1, c2 = st.columns([1, 3])
    c1.metric("Сигнал", label)
    c2.metric("Балл", f"{score:.2f} / 1.00")

    justification = rec.get("justification", "")
    if justification:
        with st.expander("Обоснование", expanded=False):
            st.text(justification)

    risks = rec.get("risks", [])
    if risks:
        with st.expander("Риски", expanded=False):
            for r in risks:
                st.markdown(f"- {r}")


def _plot_yield_curve(result: dict) -> None:
    """Рисует кривую доходности из результатов NSS + интерпретация."""
    if not result.get("success"):
        st.error(f"Ошибка подгонки: {result.get('error', '—')}")
        return

    spot = result.get("spot_rates", {})
    if not spot:
        return

    # ── Интерпретация кривой (на понятном языке) ──
    st.subheader("Что это значит для вас")

    spot_vals = sorted([(float(k), v * 100) for k, v in spot.items()])
    short_rate = spot_vals[0][1] if spot_vals else 0
    long_rate = spot_vals[-1][1] if spot_vals else 0
    mid_rate = 0
    for k, v in spot_vals:
        if k >= 2.0:
            mid_rate = v
            break

    # Определяем форму кривой
    if long_rate < short_rate - 0.5:
        shape = "перевёрнутая"
        shape_explain = (
            "Кривая **перевёрнутая** — долгосрочные ставки ниже краткосрочных. "
            "Рынок ожидает **снижения ключевой ставки ЦБ** в ближайшие 1-2 года. "
            "Это значит, что сейчас **выгоднее покупать длинные ОФЗ** — "
            "при снижении ставок их цена вырастет, и вы заработаете больше."
        )
    elif long_rate > short_rate + 1.5:
        shape = "крутая"
        shape_explain = (
            "Кривая **крутая** — длинные ставки значительно выше коротких. "
            "Рынок ожидает, что ставки ЦБ останутся высокими или вырастут. "
            "**Короткие ОФЗ (до 2 лет)** — надёжнее, а доходность и так высокая. "
            "Длинные облигации风险更大, но платят премию за срок."
        )
    else:
        shape = "нормальная"
        shape_explain = (
            "Кривая **нормальной формы** — ставки растут с увеличением срока, "
            "но без экстремальных перекосов. Рынок ждёт **стабильности ключевой ставки**. "
            "Подходящий момент для **размешения по срокам** — и короткие, и длинные "
            "облигации дают адекватную доходность."
        )

    # Блок с ключевыми выводами
    col1, col2, col3 = st.columns(3)
    col1.metric("Короткий край (0.25-0.5 лет)", f"{short_rate:.1f}%",
                help="Текущая短-term ставка — близка к ключевой ставке ЦБ")
    col2.metric("Середина кривой (2-3 года)", f"{mid_rate:.1f}%",
                help="Ожидания рынка на средний горизонт")
    col3.metric("Длинный край (10+ лет)", f"{long_rate:.1f}%",
                help="Долгосрочные ожидания — включают премию за срок")

    # Спред длинный минус короткий
    spread_long_short = long_rate - short_rate
    st.markdown(
        f"**Форма кривой:** {shape}  \n"
        f"**Спред Д-К:** {spread_long_short:+.1f} п.п.  \n\n"
        f"{shape_explain}"
    )

    st.divider()

    # ── График ──
    st.subheader("Кривая доходности ОФЗ")
    spot_df = pd.DataFrame(spot_vals, columns=["Срок (лет)", "Ставка (%)"])

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(spot_df["Срок (лет)"], spot_df["Ставка (%)"], "b-o",
            label="NSS спот-ставка", linewidth=2)

    bond_results = result.get("bond_results", [])
    if bond_results:
        obs = pd.DataFrame(bond_results)
        ax.scatter(obs["maturity_years"], obs["ytm_observed"] * 100,
                   color="red", s=60, zorder=5, label="Наблюдаемые YTM")

    ax.set_xlabel("Срок до погашения (лет)")
    ax.set_ylabel("Доходность (%)")
    ax.set_title("Кривая доходности ОФЗ (Nelson-Siegel-Svensson)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    st.pyplot(fig)
    plt.close(fig)

    # ── Что делать с этой информацией ──
    with st.expander("Как использовать кривую доходности", expanded=False):
        st.markdown("""
        **Если вы покупаете ОФЗ:**
        - Кривая **крутая** → покупайте короткие (1-3 года): высокая доходность + минимальный риск по цене
        - Кривая **перевёрнутая** → покупайте длинные (5-10+ лет): при снижении ставок цена вырастет
        - Кривая **плоская** → нет явного преимущества по срокам, ориентируйтесь на доходность

        **Сравнение с вашей альтернативой:**
        - Ключевая ставка ЦБ — это «безрисковая» ставка (депозиты, короткие ОФЗ)
        - Если доходность длинного ОФЗ = 15%, а ключевая = 21% → зачем брать длинный?
        - Но если вы ждёте снижения ставки — длинный ОФЗ даст больше прироста капитала
        """)

    # Параметры NSS
    with st.expander("Параметры NSS (для аналитиков)", expanded=False):
        p1, p2, p3 = st.columns(3)
        p1.metric("β₀ (долгосрочный)", f"{result.get('beta0', 0):.6f}")
        p2.metric("β₁ (наклон)", f"{result.get('beta1', 0):.6f}")
        p3.metric("β₂ (средняя кривизна)", f"{result.get('beta2', 0):.6f}")
        p4, p5, p6 = st.columns(3)
        p4.metric("β₃ (долгоср. кривизна)", f"{result.get('beta3', 0):.6f}")
        p5.metric("τ₁", f"{result.get('tau1', 0):.4f}")
        p6.metric("τ₂", f"{result.get('tau2', 0):.4f}")

        c1, c2 = st.columns(2)
        c1.metric("R²", f"{result.get('r_squared', 0):.4f}")
        c2.metric("RMSE", f"{result.get('rmse', 0):.4f}")

    # Таблица bond_results
    bond_results = result.get("bond_results", [])
    if bond_results:
        with st.expander("Пооблигационные остатки", expanded=False):
            res_df = pd.DataFrame(bond_results)
            res_df["ytm_observed"] = res_df["ytm_observed"] * 100
            res_df["ytm_fitted"] = res_df["ytm_fitted"] * 100
            res_df["residual"] = res_df["residual"] * 100
            st.dataframe(
                res_df[["name", "maturity_years", "ytm_observed", "ytm_fitted", "residual"]],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "name": "Облигация",
                    "maturity_years": "Срок (лет)",
                    "ytm_observed": "YTM набл. (%)",
                    "ytm_fitted": "YTM модель (%)",
                    "residual": "Остаток (%)",
                },
            )


def _render_scenario_table(results: dict) -> None:
    """Таблица сценарного анализа.

    ``results`` — словарь ``{scenario_key: {price_change_pct, pnl_rub,
    new_price, probability_estimate}}`` из ``ScenarioAnalyzer.analyze_bond()``.
    """
    if not results:
        st.warning("Нет данных по сценариям.")
        return

    from scenario_analysis import ScenarioAnalyzer

    rows = []
    for key, s in results.items():
        rows.append({
            "Сценарий": ScenarioAnalyzer._scenario_display_name(key),
            "Δ цены (%)": s.get("price_change_pct", 0),
            "P&L (₽)": s.get("pnl_rub", 0),
            "Новая цена (%)": s.get("new_price", 0),
            "Вероятность": s.get("probability_estimate", "—"),
        })

    df = pd.DataFrame(rows)

    def color_pnl(val):
        if isinstance(val, (int, float)):
            color = "green" if val > 0 else "red" if val < 0 else ""
            return f"color: {color}"
        return ""

    st.dataframe(
        df.style.map(color_pnl, subset=["P&L (₽)"]),
        use_container_width=True,
        hide_index=True,
    )


# ── Экраны ────────────────────────────────────────────────────────────────────

def screen_isin_analysis() -> None:
    """Экран: Анализ по ISIN."""
    st.header("Анализ облигации по ISIN")

    isin_input = st.text_input(
        "ISIN",
        placeholder="RU000A1038V6",
        help="Введите ISIN облигации (например, RU000A1038V6 — ОФЗ 26238)",
    )

    if not isin_input:
        st.info("Введите ISIN для запуска анализа.")
        return

    isin = isin_input.strip().upper()

    with st.spinner(f"Загрузка данных для {isin}..."):
        try:
            bd = _fetcher().get_bond_data(isin)
        except ValueError as e:
            st.error(f"Ошибка: {e}")
            return
        except Exception as e:
            st.error(f"Непредвиденная ошибка при загрузке: {e}")
            return

        if not bd.get("isin_listed", False):
            st.error(f"ISIN {isin} не найден или не торгуется на MOEX.")
            return

        cr = _calc().calculate_all(
            bd,
            tax_rate=_cfg().tax_rate,
            commission_rate=_cfg().broker_commission,
        )
        rec = _rec().rate(bd, cr)

    # ── Отображение ──
    _render_metric_cards(bd, cr)
    st.divider()
    _render_basic_info(bd)
    st.divider()
    _render_extended_metrics(bd, cr)
    st.divider()
    _render_recommendation(rec)

    # Данные для скачивания
    with st.expander("Данные bond_data (JSON)", expanded=False):
        import json
        st.code(json.dumps(bd, ensure_ascii=False, indent=2, default=str), language="json")


def screen_yield_curve() -> None:
    """Экран: Кривая доходности ОФЗ."""
    st.header("Кривая доходности ОФЗ")
    st.markdown("""
    Кривая доходности показывает, **сколько платят облигации за разные сроки**.
    Она помогает понять: **ждёт ли рынок снижения ставок ЦБ** и **какие ОФЗ выгоднее покупать сейчас**.
    """)
    st.caption("Данные — все торгуемые ОФЗ на MOEX. Модель Nelson-Siegel-Svensson.")

    with st.spinner("Загрузка ОФЗ-бумаг и построение кривой..."):
        ofz_bonds = _fetcher().get_board_bonds_full("TQOB", limit=200)
        if len(ofz_bonds) < 4:
            st.error(f"Недостаточно ОФЗ: найдено {len(ofz_bonds)}, нужно ≥4")
            return

        ofz_data = []
        for bond in ofz_bonds:
            try:
                isin = bond.get("ISIN", "")
                if not isin:
                    continue
                # Цена: LAST или PREVPRICE или MARKETPRICE2
                price = None
                for field in ("LAST", "PREVPRICE", "MARKETPRICE2", "MARKETPRICETODAY"):
                    val = bond.get(field)
                    if val is not None and val != "null" and float(val) > 0:
                        price = float(val)
                        break
                if price is None or price <= 0:
                    continue

                # YTM из marketdata (YIELD или YIELDATPREVWAPRICE)
                ytm = None
                raw_ytm = bond.get("YIELD")
                if raw_ytm is not None and raw_ytm != "null" and raw_ytm != "" and raw_ytm != 0:
                    try:
                        ytm = float(raw_ytm) / 100.0  # в десятичную долю
                    except (ValueError, TypeError):
                        pass
                # Если YIELD = 0, пробуем YIELDATPREVWAPRICE (так для большинства OFZ)
                if ytm is None or ytm <= 0:
                    raw_ytm = bond.get("YIELDATPREVWAPRICE")
                    if raw_ytm is not None and raw_ytm != "null" and raw_ytm != "":
                        try:
                            ytm = float(raw_ytm) / 100.0
                        except (ValueError, TypeError):
                            pass

                # Дней до погашения
                days_to_maturity = 0
                matdate = bond.get("MATDATE", "")
                if matdate:
                    try:
                        from datetime import datetime
                        md = datetime.strptime(matdate, "%Y-%m-%d").date()
                        days_to_maturity = (md - __import__("datetime").date.today()).days
                    except (ValueError, TypeError):
                        pass
                if days_to_maturity <= 0:
                    try:
                        days_to_maturity = int(bond.get("DURATION", 0))
                    except (ValueError, TypeError):
                        pass
                if days_to_maturity <= 0:
                    continue

                maturity_years = days_to_maturity / 365.0
                name = bond.get("SHORTNAME", isin)

                # Coupon rate
                coupon_rate = 0.0
                for src in (bond,):
                    cv = src.get("COUPONPERCENT")
                    if cv is not None and cv != "null" and cv != "":
                        try:
                            coupon_rate = float(cv)
                        except (ValueError, TypeError):
                            pass
                        break

                if ytm is not None and ytm > 0:
                    ofz_data.append({
                        "isin": isin,
                        "face_value": 1000,
                        "coupon_rate": coupon_rate,
                        "coupon_value": 0,
                        "coupon_frequency": 2,
                        "days_to_maturity": days_to_maturity,
                        "price": price,
                        "name": name,
                        "maturity_years": maturity_years,
                        "ytm": ytm,
                    })
            except Exception:
                continue

        result = YieldCurve.build_from_ofz(ofz_data)

    _plot_yield_curve(result)

    # Таблица OFZ
    if ofz_data:
        with st.expander(f"Данные по {len(ofz_data)} ОФЗ", expanded=False):
            st.dataframe(
                pd.DataFrame(ofz_data),
                use_container_width=True,
                hide_index=True,
            )


def screen_scenario() -> None:
    """Экран: Сценарный анализ."""
    st.header("Сценарный анализ")
    st.markdown("""
    **Что это:** Прогноз того, **сколько вы заработаете или потеряете** при разных изменениях
    ключевой ставки ЦБ. Показывает, как изменится цена облигации и ваш доход в рублях.

    **Зачем нужно:** Чтобы понять, **какой риск вы берёте** при покупке этой облигации.
    Если ставка ЦБ упадёт — вы заработаете больше. Если вырастет — можете потерять.
    """)

    isin_input = st.text_input(
        "ISIN для сценарного анализа",
        placeholder="RU000A1038V6",
        key="scenario_isin",
    )

    if not isin_input:
        st.info("Введите ISIN для запуска сценарного анализа.")
        return

    isin = isin_input.strip().upper()

    with st.spinner(f"Анализ сценариев для {isin}..."):
        try:
            bd = _fetcher().get_bond_data(isin)
        except ValueError as e:
            st.error(f"Ошибка: {e}")
            return

        cr = _calc().calculate_all(
            bd,
            tax_rate=_cfg().tax_rate,
            commission_rate=_cfg().broker_commission,
        )
        results = ScenarioAnalyzer.analyze_bond(bd, cr, None)

    name = bd.get("shortname", isin)
    price = bd.get("current_price", 0)
    face_value = bd.get("face_value", 1000)
    price_value = price / 100.0 * face_value
    ytm = bd.get("yield_moex", 0)

    st.subheader(name)

    # Краткая карточка
    c1, c2, c3 = st.columns(3)
    c1.metric("Текущая цена", f"{price:.2f}%  ({price_value:,.0f} ₽)")
    c2.metric("Доходность к погашению", f"{ytm:.2f}%")
    c3.metric("Номинал", f"{face_value:,.0f} ₽")

    st.divider()

    # ── Простое объяснение сценариев ──
    st.subheader("Что произойдёт с вашими деньгами")
    st.markdown(
        "Ниже — прогноз изменения цены облигации при разных решениях ЦБ. "
        "**P&L** — это ваша прибыль или убыток в рублях на одну облигацию."
    )

    _render_scenario_table(results)

    # Вероятностно-взвешенная доходность
    prob = ScenarioAnalyzer.probability_weighted_return(results, None)
    st.divider()

    st.subheader("Итоговый прогноз (с учётом вероятностей)")

    exp_ret = prob.get("expected_return_pct", 0)
    std_dev = prob.get("std_dev_pct", 0)
    sharpe = prob.get("sharpe_ratio_estimate", 0)

    # Интерпретация для пользователя
    st.markdown(
        f"Мы просчитали **9 разных сценариев** развития событий и взвесили каждый "
        f"по вероятности. Вот что получилось:"
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Ожидаемая доходность", f"{exp_ret:.2f}%",
              help="Средневзвешенная доходность по всем сценариям")
    c2.metric("Волатильность", f"{std_dev:.2f}%",
              help="Насколько сильно доходность может отклониться от ожидаемой")
    c3.metric("Коэфф. Шарпа", f"{sharpe:.2f}",
              help="Чем выше — тем лучше доходность на единицу риска")

    # Понятная интерпретация
    st.divider()
    if exp_ret > 0:
        annual_income_est = exp_ret / 100.0 * price_value
        st.success(
            f"**Вывод:** С учётом всех сценариев, эта облигация, скорее всего, "
            f"принесёт **~{exp_ret:.1f}% годовых** (или **~{annual_income_est:,.0f} ₽** "
            f"на облигацию за год). "
            f"{'Риск невысокий' if std_dev < 3 else 'Риск умеренный — возможны колебания цены'}."
        )
    elif exp_ret < 0:
        st.error(
            f"**Вывод:** По нашим расчётам, эта облигация может **потерять ~{abs(exp_ret):.1f}%** "
            f"при неблагоприятном развитии событий. Стоит рассмотреть альтернативы."
        )
    else:
        st.info("**Вывод:** Доходность примерно равна риску. Облигация нейтральна.")

    with st.expander("Как читать эту таблицу?", expanded=False):
        st.markdown("""
        - **Базовый сценарий** — ставка ЦБ не меняется. Облигация растёт на купонах.
        - **Снижение КС на 1-2%** — ЦБ снижает ставку. Цена длинных облигаций **вырастает** (вы зарабатываете на курсовой разнице).
        - **Повышение КС на 1-2%** — ЦБ повышает ставку. Цена облигаций **падает** (вы теряете на бумаге, но купоны идут).
        - **Рецессия** — ставка падает, но растут кредитные спреды. Смешанный эффект.
        - **Рост спредов** — рынок требует премию за риск. Цены падают.
        - **Вероятность** — насколько реален этот сценарий: «очень вероятно» = базовый, «стресс-сценарий» = маловероятно, но возможно.
        """)


def screen_top() -> None:
    """
    Экран: Топ-рекомендации — лучшие облигации для покупки прямо сейчас.
    Объединяет логику старого 'Топ-рекомендации' и 'Что купить на рынке'.
    """
    st.header("🏆 Топ-рекомендации: что купить прямо сейчас")
    st.markdown("""
    Анализируем облигации, **доступные в Т-Инвестициях**, и находим лучшие по сочетанию
    **доходности, надёжности и срока**. Каждой присваивается **балл (0–1)** —
    чем выше, тем привлекательнее бумага для покупки.

    **Как читать:**
    - 🟢 **Лучшие** (балл ≥ 0.75) — высокая доходность при приемлемом риске
    - 🟡 **Хорошие** (балл 0.55–0.74) — достойный вариант для рассмотрения
    - 🔵 **Интересные** (балл < 0.55) — есть нюансы, читайте обоснование
    """)

    # ── Настройки ──
    col_f1, col_f2, col_f3, col_f4 = st.columns([1, 1, 1, 1])
    with col_f1:
        n = st.slider("Сколько показать", min_value=3, max_value=30, value=10, step=1)
    with col_f2:
        min_ytm = st.slider("Мин. доходность (YTM, %)", min_value=0.0, max_value=30.0, value=8.0, step=0.5,
                            help="Доходность к погашению по данным MOEX (до вычета НДФЛ). "
                                 "Чистая доходность после налога и комиссии — см. «Эфф. YTM» в карточке бумаги")
    with col_f3:
        max_duration = st.slider("Макс. дюрация (лет)", min_value=0.5, max_value=30.0, value=15.0, step=0.5,
                                 help="Дюрация = чувствительность цены к ставке. Чем меньше — тем стабильнее цена")
    with col_f4:
        min_days = st.slider("Мин. дней до погашения", min_value=30, max_value=3650, value=90, step=10,
                             help="Облигации с коротким сроком (<90 дн.) дают копеечную прибыль и не окупают комиссий")

    show_ofz = st.checkbox("✅ Включить ОФЗ (гос. облигации)", value=False,
                           help="ОФЗ — самые надёжные, но доходность обычно ниже")

    reliability = st.selectbox(
        "🔰 Уровень надёжности",
        options=["Все уровни", "Высоконадёжные (листинг 1)", "Средней надёжности (листинг 2)", "Низкой надёжности (листинг 3)"],
        index=0,
        help="Фильтр по уровню листинга MOEX: 1 = высший, 2 = стандарт, 3 = низколиквидные"
    )
    reliability_level: int | None = None
    if reliability == "Высоконадёжные (листинг 1)":
        reliability_level = 1
    elif reliability == "Средней надёжности (листинг 2)":
        reliability_level = 2
    elif reliability == "Низкой надёжности (листинг 3)":
        reliability_level = 3

    # ── Загрузка списка облигаций ──
    with st.spinner("Загрузка облигаций с MOEX..."):
        bonds_corp = _fetcher().get_board_bonds_full("TQCB", limit=300)
        bonds_full = list(bonds_corp)

        if show_ofz:
            bonds_ofz = _fetcher().get_board_bonds_full("TQOB", limit=100)
            bonds_full.extend(bonds_ofz)
            st.info(f"Загружено: {len(bonds_corp)} корпоративных + {len(bonds_ofz)} ОФЗ")
        else:
            st.info(f"Загружено: {len(bonds_corp)} корпоративных облигаций")

    # ── Предфильтрация ──
    candidates = []
    for b in bonds_full:
        isin = b.get("ISIN", "")
        ytm_raw = b.get("YIELD")
        if not isin or ytm_raw is None:
            continue
        try:
            ytm_val = float(ytm_raw)
        except (TypeError, ValueError):
            continue
        if ytm_val < min_ytm:
            continue

        # Предфильтрация по надёжности (LISTLEVEL из board data)
        if reliability_level is not None:
            try:
                b_lvl_raw = b.get("LISTLEVEL")
                b_lvl_int = int(b_lvl_raw) if b_lvl_raw not in (None, "null", "") else None
            except (ValueError, TypeError):
                b_lvl_int = None
            if b_lvl_int is not None and b_lvl_int != reliability_level:
                continue

        candidates.append(b)

    if not candidates:
        msg = f"Не найдено облигаций с доходностью ≥ {min_ytm}%"
        if reliability_level is not None:
            msg += f" и уровнем надёжности {reliability_level}"
        msg += ".\n\nПопробуйте уменьшить минимальную доходность или расширить фильтры."
        st.warning(msg)
        return

    # ── Фильтр по доступности в Т-Инвестициях ──
    tinv_isins: set[str] = set()
    try:
        tinv_bonds = _tinvest().get_all_bonds()
        tinv_isins = {
            b["isin"] for b in tinv_bonds
            if b.get("isin") and b.get("buyAvailableFlag")
        }
    except Exception:
        pass

    if tinv_isins:
        before = len(candidates)
        candidates = [b for b in candidates if b.get("ISIN", "") in tinv_isins]
        filtered_out = before - len(candidates)
        if not candidates:
            st.warning(
                "Все облигации с подходящей доходностью недоступны в Т-Инвестициях.\n\n"
                "Попробуйте уменьшить минимальную доходность или включить ОФЗ."
            )
            return

    # ── Глубокий анализ ──
    scored = []
    to_analyze = candidates  # анализируем ВСЕХ кандидатов, прошедших предфильтр
    st.caption(f"Найдено {len(candidates)} кандидатов с YTM ≥ {min_ytm}%, "
               f"анализируем все {len(to_analyze)} бумаг.")
    progress = st.progress(0, text="Анализ облигаций...")

    for i, bond_entry in enumerate(to_analyze):
        isin = bond_entry.get("ISIN", "")
        progress.progress(
            (i + 1) / len(to_analyze),
            text=f"Анализ {i + 1}/{len(to_analyze)}: {isin}...",
        )

        try:
            bd = _fetcher().get_bond_data(isin)
            if not bd or (bd.get("current_price") or 0) <= 0:
                continue

            cr = _calc().calculate_all(
                bd,
                tax_rate=_cfg().tax_rate,
                commission_rate=_cfg().broker_commission,
            )
            rec = _rec().rate(bd, cr)

            signal = rec.get("signal", "N/A")
            score = rec.get("score", 0)

            face_value = bd.get("face_value", 1000)
            current_price = bd.get("current_price", 0)
            price_value = current_price / 100.0 * face_value
            nkd = bd.get("nkd", 0)
            coupon_value = bd.get("coupon_value", 0)
            coupon_frequency = bd.get("coupon_frequency", 4)
            days_to_maturity = bd.get("days_to_maturity", 0)
            ytm = bd.get("yield_moex", 0)
            eff_ytm = cr.get("effective_ytm", {}).get("ytm_pct", 0)
            if eff_ytm <= 0:
                eff_ytm = ytm
            mod_dur = cr.get("modified_duration_years", 0)

            if coupon_value <= 0 or ytm <= 0 or days_to_maturity <= 0:
                continue
            if days_to_maturity < min_days:
                continue
            if mod_dur > max_duration:
                continue

            annual_coupon_rub = coupon_value * coupon_frequency
            current_yield_pct = (annual_coupon_rub / price_value * 100) if price_value > 0 else 0
            commission = price_value * _cfg().broker_commission
            total_cost = price_value + nkd + commission
            years_to_maturity = days_to_maturity / 365.0
            # Считаем ТОЛЬКО полные оставшиеся купоны (не дробные)
            coupon_period_days = 365.0 / coupon_frequency if coupon_frequency > 0 else 365.0
            remaining_coupons = int(days_to_maturity / coupon_period_days)
            total_coupon_income = coupon_value * remaining_coupons
            capital_gain = face_value - price_value
            gross_income = total_coupon_income + capital_gain
            tax = max(0, gross_income * _cfg().tax_rate)
            net_income = gross_income - tax
            roi_pct = (net_income / total_cost * 100) if total_cost > 0 else 0

            # Минимальная реальная доходность: не менее 1% за весь период или 0.5%/год
            min_roi = max(1.0, years_to_maturity * 0.5)
            if roi_pct < min_roi:
                continue

            months = max(1, days_to_maturity / 30.44)
            monthly_income = net_income / months

            portfolio_size = 100
            portfolio_cost = total_cost * portfolio_size
            portfolio_annual_income = annual_coupon_rub * portfolio_size
            portfolio_total_profit = net_income * portfolio_size

            scored.append({
                "rank": 0,
                "name": bd.get("shortname", isin),
                "isin": isin,
                "score": score,
                "signal": signal,
                "price_pct": current_price,
                "price_rub": price_value,
                "total_cost_1": total_cost,
                "ytm": ytm,
                "eff_ytm": eff_ytm,
                "spread_over_rf": ytm - (_cfg().risk_free_rate or 0.145) * 100,
                "current_yield": current_yield_pct,
                "coupon_value": coupon_value,
                "coupon_freq": coupon_frequency,
                "annual_coupon": annual_coupon_rub,
                "days_to_maturity": days_to_maturity,
                "years": years_to_maturity,
                "total_profit_rub": net_income,
                "roi_pct": roi_pct,
                "monthly_income": monthly_income,
                "portfolio_cost_100": portfolio_cost,
                "portfolio_annual_100": portfolio_annual_income,
                "portfolio_profit_100": portfolio_total_profit,
                "duration": mod_dur,
                "justification": rec.get("justification", ""),
                "risks": rec.get("risks", []),
            })
        except Exception:
            continue

    progress.empty()

    if not scored:
        hints = []
        if min_ytm > 0:
            hints.append(f"• уменьшить мин. доходность (сейчас {min_ytm}%)")
        if reliability_level is not None:
            hints.append(f"• расширить уровень надёжности (сейчас уровень {reliability_level})")
        if max_duration < 30:
            hints.append(f"• увеличить макс. дюрацию (сейчас {max_duration} лет)")
        if min_days > 90:
            hints.append(f"• уменьшить мин. дней до погашения (сейчас {min_days})")
        st.warning(
            "Ни одна облигация не прошла все фильтры.\n\n"
            + "\n".join(hints)
            + "\n\n💡 Подсказка: начните с «Все уровни» и мягких фильтров, затем ужесточайте."
        )
        return

    # ── Сортировка: по баллу recommender (уже включает всё: доходность, риск, ликвидность) ──
    scored.sort(key=lambda x: x["score"], reverse=True)
    for i, row in enumerate(scored[:n], 1):
        row["rank"] = i

    top = scored[:n]

    # ── Сводка по отобранным ──
    avg_ytm = sum(r["ytm"] for r in top) / len(top)
    avg_roi = sum(r["roi_pct"] for r in top) / len(top)
    avg_years = sum(r["years"] for r in top) / len(top)
    avg_dur = sum(r["duration"] for r in top) / len(top)
    rf_rate = (_cfg().risk_free_rate or 0.145) * 100

    st.success(f"🎯 Отобрано **{len(top)}** лучших из {len(scored)} проанализированных")

    st.subheader("📊 Сводка портфеля из отобранных облигаций")
    c1, c2, c3, c4, c5 = st.columns(5)
    _metric_card(c1, "Облигаций", f"{len(top)}", "white",
                 "Количество отобранных облигаций")
    # YTM: зелёный если выше КС
    ytm_color = "green" if avg_ytm >= rf_rate else "red"
    _metric_card(c2, "Средн. YTM", f"{avg_ytm:.1f}% ({avg_ytm - rf_rate:+.1f}% к КС)",
                 ytm_color, f"Доходность {'выше' if avg_ytm >= rf_rate else 'ниже'} безрисковой ставки")
    # ROI: зелёный если положительный
    roi_color = "green" if avg_roi > 0 else "red"
    _metric_card(c3, "Средн. ROI за срок", f"{avg_roi:.0f}%",
                 roi_color, "Средняя доходность за весь период владения")
    # Срок: без цвета (информативно)
    _metric_card(c4, "Средн. срок", f"{avg_years:.1f} лет", "white",
                 "Средний срок до погашения")
    # Дюрация: зелёная если в пределах нормы
    horizon_y = _cfg().investment_horizon_days / 365.0
    dur_r = avg_dur / horizon_y if horizon_y > 0 else 1
    dur_c = "green" if dur_r <= 1.0 else ("orange" if dur_r <= 1.5 else "red")
    _metric_card(c5, "Средн. дюрация", f"{avg_dur:.1f} лет",
                 dur_c, "Чувствительность к ставкам. Ниже — стабильнее")

    # ── Карточки разбивки по сигналам ──
    buy_bonds = [b for b in top if b["signal"] == "ПОКУПАТЬ"]
    hold_bonds = [b for b in top if b["signal"] in ("ДЕРЖАТЬ", "ДОКУПАТЬ")]
    sell_bonds = [b for b in top if b["signal"] == "ПРОДАВАТЬ"]

    if buy_bonds:
        st.subheader("✅ Рекомендуем к покупке")
        for bond in buy_bonds:
            _render_bond_card(bond, expanded=False)
    if hold_bonds:
        st.subheader("🟡 Можно рассмотреть")
        for bond in hold_bonds:
            _render_bond_card(bond, expanded=False)
    if sell_bonds:
        st.subheader("🔴 Не рекомендуется")
        for bond in sell_bonds:
            _render_bond_card(bond, expanded=False)

    if not buy_bonds and not hold_bonds:
        st.info("💡 Все облигации в нейтральной зоне. Попробуйте изменить фильтры.")


def _render_bond_card(bond: dict, expanded: bool = False) -> None:
    """Отрисовать карточку одной облигации."""
    if bond["score"] >= 0.75:
        tag = "🟢 Лучшая"
    elif bond["score"] >= 0.55:
        tag = "🟡 Хорошая"
    else:
        tag = "🔵 Интересная"

    signal = bond["signal"]
    if signal == "ПОКУПАТЬ":
        sig_tag = "✅ ПОКУПАТЬ"
    elif signal == "ДОКУПАТЬ":
        sig_tag = "🟠 ДОКУПАТЬ"
    elif signal == "ДЕРЖАТЬ":
        sig_tag = "🟡 ДЕРЖАТЬ"
    else:
        sig_tag = "🔴 ПРОДАВАТЬ"

    with st.expander(
        f"{tag} #{bond['rank']} {bond['name']} — {bond['ytm']:.1f}% YTM, "
        f"{bond['price_rub']:,.0f} ₽, {sig_tag}",
        expanded=expanded,
    ):
        st.markdown(f"**ISIN:** `{bond['isin']}` | **Балл:** {bond['score']:.2f}/1.00")

        # Первая строка метрик
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Цена", f"{bond['price_pct']:.2f}%  ({bond['price_rub']:,.0f} ₽)",
                    help="Рыночная цена в % от номинала. Ниже 100% = дисконт")
        col2.metric("YTM (доходность)", f"{bond['ytm']:.1f}%",
                    delta=f"{bond.get('spread_over_rf', 0):+.1f}% к КС",
                    delta_color="normal",
                    help="Доходность к погашению (данные MOEX). Чем выше — тем лучше")
        col3.metric("Эфф. YTM (на руки)", f"{bond['eff_ytm']:.1f}%",
                    help="Доходность после вычета НДФЛ и комиссии. Реальная доходность")
        col4.metric("Дюрация", f"{bond['duration']:.1f} лет",
                    help="Чувствительность к ставке: +1% → цена ↓ на дюрацию %")

        # Вторая строка
        col5, col6, col7, col8 = st.columns(4)
        col5.metric("Купон", f"{bond['coupon_value']:.0f} ₽ × {bond['coupon_freq']}/год",
                    help="Размер купона и периодичность выплат")
        col6.metric("Прибыль за срок", f"{bond['total_profit_rub']:+,.0f} ₽",
                    help="Чистая прибыль на 1 облигацию за весь период")
        col7.metric("ROI", f"{bond['roi_pct']:.0f}%",
                    help="Доходность в % от вложенных средств за весь срок")
        col8.metric("До погашения", f"{bond['days_to_maturity']} дн. ({bond['years']:.1f} лет)")

        # Инвестиционный кейс
        buy_cost = bond['total_cost_1']
        st.markdown("---")
        st.markdown(
            f"**💰 Стоимость 1 облигации:** {buy_cost:,.0f} ₽ (цена {bond['price_rub']:,.0f} + НКД + комиссия)"
        )
        st.markdown(
            f"**📦 Пример на 100 шт:** вложения {bond['portfolio_cost_100']:,.0f} ₽, "
            f"годовой купон {bond['portfolio_annual_100']:,.0f} ₽, "
            f"доход **{bond['portfolio_profit_100']:+,.0f} ₽** за {bond['years']:.1f} лет"
        )

        # Обоснование и риски
        if bond["justification"]:
            st.markdown(f"**💡 Почему покупать:** {bond['justification']}")
        if bond["risks"]:
            st.markdown("**⚠️ Риски:**")
            for risk in bond["risks"]:
                st.markdown(f"- {risk}")

        # Внешняя аналитика
        isin_val = bond.get("isin", "")
        name_val = bond.get("name", "")
        if isin_val:
            st.markdown("---")
            st.markdown("**🔗 Аналитика из внешних источников:**")
            st.markdown(
                f"[📊 Smart-lab: {name_val}](https://smart-lab.ru/q/bonds/{isin_val}/)  |  "
                f"[🏦 Rusbonds](https://rusbonds.ru/search?query={isin_val})  |  "
                f"[📈 MOEX](https://www.moex.com/ru/issue.aspx?code={isin_val})"
            )


# ── Экран: Анализ портфеля ─────────────────────────────────────────────────

def _portfolio_load_data(csv_path: Path) -> dict:
    """Загрузить CSV, получить данные MOEX для каждой облигации, кэшировать в session_state."""
    import os
    mtime = os.path.getmtime(csv_path)
    cache_key = "portfolio_data_cache"
    cached = st.session_state.get(cache_key, {})
    if cached.get("mtime") == mtime and cached.get("records"):
        return cached

    portfolio_df = pd.read_csv(csv_path)
    if portfolio_df.empty:
        return {"mtime": mtime, "records": [], "portfolio_scenarios": {}, "efficiency": {}}

    isin_col = "isin" if "isin" in portfolio_df.columns else portfolio_df.columns[0]
    qty_col = "quantity" if "quantity" in portfolio_df.columns else None
    amount_col = "amount" if "amount" in portfolio_df.columns else None

    records = []
    progress = st.progress(0, text="Загрузка данных с MOEX...")
    total = len(portfolio_df)

    for row_num, (_, row) in enumerate(portfolio_df.iterrows(), 1):
        isin = str(row.get(isin_col, "")).strip()
        if not isin or len(isin) < 5:
            continue
        progress.progress(row_num / total, text=f"Анализ {isin} ({row_num}/{total})...")

        try:
            bd = _fetcher().get_bond_data(isin)
            if not bd or (bd.get("current_price") or 0) <= 0:
                continue
            cr = _calc().calculate_all(
                bd, tax_rate=_cfg().tax_rate, commission_rate=_cfg().broker_commission,
            )
            sr = ScenarioAnalyzer.analyze_bond(bd, cr)
            forecast = ScenarioAnalyzer.probability_weighted_return(sr)
            rec = _rec().rate(bd, cr, sr)

            qty_val = row.get(qty_col, 0) if qty_col else 0
            qty = float(qty_val) if qty_val else 0.0
            amount_val = row.get(amount_col, 0) if amount_col else 0
            cost_basis = float(amount_val) if amount_val else 0.0

            face_value = bd.get("face_value", 1000)
            current_price = bd.get("current_price", 0)
            price_value = current_price / 100.0 * face_value
            current_value = price_value * qty
            avg_buy_price = cost_basis / qty if qty > 0 else 0
            unrealized_pnl = current_value - cost_basis if cost_basis > 0 else 0
            unrealized_pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0

            records.append({
                "isin": isin,
                "name": bd.get("shortname", isin),
                "quantity": qty,
                "cost_basis": cost_basis,
                "avg_buy_price": avg_buy_price,
                "current_value": current_value,
                "unrealized_pnl": unrealized_pnl,
                "unrealized_pnl_pct": unrealized_pnl_pct,
                "weight_pct": 0.0,  # заполним после
                "bond_data": bd,
                "calc_results": cr,
                "scenario_results": sr,
                "scenario_forecast": forecast,
                "recommendation": rec,
            })
        except Exception:
            continue

    progress.empty()

    # Рассчитываем веса
    total_value = sum(r["current_value"] for r in records)
    if total_value > 0:
        for r in records:
            r["weight_pct"] = r["current_value"] / total_value * 100

    # Портфельный сценарный анализ
    portfolio_for_scenarios = [
        {"bond_data": r["bond_data"], "calc_results": r["calc_results"], "quantity": r["quantity"]}
        for r in records if r["quantity"] > 0
    ]
    portfolio_scenarios = {}
    if portfolio_for_scenarios:
        try:
            portfolio_scenarios = ScenarioAnalyzer.analyze_portfolio(portfolio_for_scenarios)
        except Exception:
            pass

    # Портфельная эффективность
    efficiency = {}
    if records:
        try:
            portfolio_for_opt = [
                {
                    "name": r["name"],
                    "weight": r["weight_pct"] / 100.0,
                    "ytm": r["bond_data"].get("yield_moex") or 0,
                    "modified_duration": r["calc_results"].get("modified_duration_years") or 0,
                    "z_spread": r["bond_data"].get("z_spread") or 0.0,
                    "convexity": r["calc_results"].get("convexity") or 0,
                }
                for r in records
            ]
            efficiency = PortfolioOptimizer.efficiency_report(portfolio_for_opt)
        except Exception:
            pass

    result = {"mtime": mtime, "records": records, "portfolio_scenarios": portfolio_scenarios, "efficiency": efficiency}
    st.session_state[cache_key] = result
    return result


def _portfolio_load_from_api() -> dict:
    """Загрузить портфель через T-Invest API, обогатить данными MOEX, кэшировать."""
    cache_key = "portfolio_api_cache"
    cached = st.session_state.get(cache_key, {})
    if cached.get("records"):
        return cached

    client = _tinvest()
    
    # Use configured account_id, fallback to first broker account
    account_id = getattr(_cfg().tinvest, "account_id", "") or None
    if not account_id:
        account_id = client.get_first_broker_account_id()
    
    api_total_value = 0.0
    api_daily_yield = 0.0
    api_expected_yield_pct = 0.0
    api_daily_yield_relative = 0.0
    api_total_bonds = 0.0
    if account_id:
        try:
            portfolio_resp = client.get_portfolio(account_id)
            if portfolio_resp:
                api_total_value = _money_to_float(portfolio_resp.get("totalAmountPortfolio", {}))
                api_daily_yield = _money_to_float(portfolio_resp.get("dailyYield", {}))
                api_expected_yield_pct = _quotation_to_float(portfolio_resp.get("expectedYield", {}))
                api_daily_yield_relative = _quotation_to_float(portfolio_resp.get("dailyYieldRelative", {}))
                api_total_bonds = _money_to_float(portfolio_resp.get("totalAmountBonds", {}))
        except Exception:
            pass
    
    positions = client.get_bond_positions(account_id=account_id)
    if not positions:
        return {"records": [], "portfolio_scenarios": {}, "efficiency": {}}

    records = []
    progress = st.progress(0, text="Загрузка данных с MOEX...")
    total = len(positions)

    for i, pos in enumerate(positions, 1):
        isin = pos.get("isin", "")
        qty = pos.get("quantity", 0)
        if not isin or qty <= 0:
            continue

        progress.progress(i / total, text=f"Анализ {pos.get('name', isin)} ({i}/{total})...")

        try:
            bd = _fetcher().get_bond_data(isin)
            if not bd or (bd.get("current_price") or 0) <= 0:
                continue
            cr = _calc().calculate_all(
                bd, tax_rate=_cfg().tax_rate, commission_rate=_cfg().broker_commission,
            )
            sr = ScenarioAnalyzer.analyze_bond(bd, cr)
            forecast = ScenarioAnalyzer.probability_weighted_return(sr)
            rec = _rec().rate(bd, cr, sr)

            # Monetary values from T-Invest API (CORRECT sources)
            avg_price_api = pos.get("average_price", 0)
            api_exp_yield_rub = pos.get("expected_yield_pct", 0)  # total P&L in RUB from API
            api_daily_rub = pos.get("daily_yield_rub", 0)  # daily P&L in RUB from API
            nkd_api = pos.get("current_nkd", 0)

            face_value = bd.get("face_value", 1000)
            price_pct = bd.get("current_price", 0)
            price_value = price_pct / 100.0 * face_value

            current_value = price_value * qty
            # Use API expected yield for P&L (more accurate than our recalculation)
            unrealized_pnl = api_exp_yield_rub if api_exp_yield_rub != 0 else (current_value - avg_price_api * qty if avg_price_api > 0 else 0)
            cost_basis = avg_price_api * qty if avg_price_api > 0 else current_value - unrealized_pnl
            unrealized_pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0

            records.append({
                "isin": isin,
                "name": bd.get("shortname", pos.get("name", isin)),
                "quantity": qty,
                "cost_basis": cost_basis,
                "avg_buy_price": avg_price_api,
                "current_value": current_value,
                "unrealized_pnl": unrealized_pnl,
                "unrealized_pnl_pct": unrealized_pnl_pct,
                "daily_pnl": api_daily_rub,
                "nkd": nkd_api,
                "weight_pct": 0.0,
                "bond_data": bd,
                "calc_results": cr,
                "scenario_results": sr,
                "scenario_forecast": forecast,
                "recommendation": rec,
            })
        except Exception:
            continue

    progress.empty()

    total_value = sum(r["current_value"] for r in records)
    if total_value > 0:
        for r in records:
            r["weight_pct"] = r["current_value"] / total_value * 100

    portfolio_for_scenarios = [
        {"bond_data": r["bond_data"], "calc_results": r["calc_results"], "quantity": r["quantity"]}
        for r in records if r["quantity"] > 0
    ]
    portfolio_scenarios = {}
    if portfolio_for_scenarios:
        try:
            portfolio_scenarios = ScenarioAnalyzer.analyze_portfolio(portfolio_for_scenarios)
        except Exception:
            pass

    efficiency = {}
    if records:
        try:
            portfolio_for_opt = [
                {
                    "name": r["name"],
                    "weight": r["weight_pct"] / 100.0,
                    "ytm": r["bond_data"].get("yield_moex") or 0,
                    "modified_duration": r["calc_results"].get("modified_duration_years") or 0,
                    "z_spread": r["bond_data"].get("z_spread") or 0.0,
                    "convexity": r["calc_results"].get("convexity") or 0,
                }
                for r in records
            ]
            efficiency = PortfolioOptimizer.efficiency_report(portfolio_for_opt)
        except Exception:
            pass

    result = {
        "records": records,
        "portfolio_scenarios": portfolio_scenarios,
        "efficiency": efficiency,
        "api_total_value": api_total_value,
        "api_daily_yield": api_daily_yield,
        "api_expected_yield_pct": api_expected_yield_pct,
        "api_daily_yield_relative": api_daily_yield_relative,
        "api_total_bonds": api_total_bonds,
    }
    st.session_state[cache_key] = result
    return result


def screen_portfolio() -> None:
    """Экран: Глубокий анализ портфеля Т-Инвестиции."""
    st.header("📊 Глубокий анализ портфеля")

    # Используем T-Invest API (broker_scraper удалён, теперь только API)
    token = _cfg().tinvest.token
    if not token:
        st.warning(
            "Токен T-Invest API не настроен.\n\n"
            "1. Перейдите в раздел **⚙️ Настройки**\n"
            "2. Введите токен API Т-Инвестиций\n"
            "3. Получить токен: https://www.tbank.ru/invest/settings/"
        )
        return

    if st.button("🔄 Загрузить с T-Invest API"):
        st.session_state.pop("portfolio_api_cache", None)
        st.rerun()

    data = _portfolio_load_from_api()
    records = data.get("records", [])
    portfolio_scenarios = data.get("portfolio_scenarios", {})
    efficiency = data.get("efficiency", {})
    api_total_value = data.get("api_total_value", 0)
    api_daily_yield = data.get("api_daily_yield", 0)
    api_expected_yield_pct = data.get("api_expected_yield_pct", 0)
    api_daily_yield_relative = data.get("api_daily_yield_relative", 0)
    api_total_bonds = data.get("api_total_bonds", 0)

    if not records:
        st.error("Не удалось загрузить данные ни для одной облигации.")
        return

    st.success(f"Проанализировано **{len(records)}** облигаций из портфеля")

    # ══════════════════════════════════════════════════════════════════════════
    # 1. СВОДКА ПОРТФЕЛЯ
    # ══════════════════════════════════════════════════════════════════════════
    st.subheader("Сводка портфеля")

    # Use API values where available (they are authoritative)
    display_total_value = api_total_value if api_total_value > 0 else sum(r["current_value"] for r in records)
    display_bonds_value = api_total_bonds if api_total_bonds > 0 else sum(r["current_value"] for r in records)
    total_pnl = sum(r["unrealized_pnl"] for r in records)
    total_cost = sum(r["cost_basis"] for r in records)
    total_qty = sum(r["quantity"] for r in records)
    total_daily_pnl = sum(r.get("daily_pnl", 0) for r in records)

    # Weighted YTM and duration (by market value)
    valid_records = [r for r in records if (r["bond_data"].get("yield_moex") or 0) > 0]
    bonds_value_valid = sum(r["current_value"] for r in valid_records) if valid_records else 1
    if valid_records:
        w_ytm_sum = sum((r["bond_data"].get("yield_moex") or 0) * r["current_value"] for r in valid_records)
        w_dur_sum = sum((r["calc_results"].get("modified_duration_years") or 0) * r["current_value"] for r in valid_records)
        avg_ytm = w_ytm_sum / bonds_value_valid if bonds_value_valid > 0 else 0
        avg_dur = w_dur_sum / bonds_value_valid if bonds_value_valid > 0 else 0
    else:
        avg_ytm = 0
        avg_dur = 0

    # Определяем пороговые значения для цветовой маркировки
    rf_rate = (_cfg().risk_free_rate or 0.145) * 100  # безрисковая ставка в %
    horizon_years = _cfg().investment_horizon_days / 365.0  # горизонт в годах

    # Row 1: Core metrics
    c1, c2, c3, c4 = st.columns(4)
    _metric_card(c1, "Бумаг", f"{len(records)}", "white",
                 "Количество облигационных позиций в портфеле")
    _metric_card(c2, "Общая стоимость", f"{display_total_value:,.0f} ₽", "white",
                 "Текущая рыночная стоимость всех облигаций")

    # P&L: зелёный при прибыли, красный при убытке
    if total_pnl != 0:
        pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
        pnl_color = "green" if total_pnl >= 0 else "red"
        _metric_card(c3, "P&L (нереализованный)", f"{total_pnl:+,.0f} ₽ ({pnl_pct:+.1f}%)",
                     pnl_color, "Прибыль/убыток от текущей цены до цены покупки (не считая купонов)")
    else:
        _metric_card(c3, "P&L (нереализованный)", "—", "white",
                     "Прибыль/убыток от текущей цены до цены покупки")

    # YTM: зелёный если выше безрисковой ставки, красный если ниже
    ytm_color = "green" if avg_ytm >= rf_rate else "red"
    _metric_card(c4, "Средн. YTM", f"{avg_ytm:.1f}%", ytm_color,
                 "Средневзвешенная доходность к погашению. "
                 f"{'Выше' if avg_ytm >= rf_rate else 'Ниже'} безрисковой ставки ({rf_rate:.1f}%) — "
                 f"{'хороший' if avg_ytm >= rf_rate else 'слабый'} показатель")

    # Row 2: Extended metrics
    c5, c6, c7, c8 = st.columns(4)
    # Дюрация: зелёная если в пределах 20-100% горизонта, красная если больше 150%
    dur_ratio = avg_dur / horizon_years if horizon_years > 0 else 1
    dur_color = "green" if dur_ratio <= 1.0 else ("orange" if dur_ratio <= 1.5 else "red")
    _metric_card(c5, "Средн. дюрация", f"{avg_dur:.1f} лет", dur_color,
                 "Средневзвешенная модифицированная дюрация. "
                 f"{'В пределах нормы' if dur_ratio <= 1.0 else 'Высокая'} относительно горизонта {horizon_years:.1f} лет")

    # Дневная доходность: зелёный при росте, красный при падении
    dy_color = "green" if api_daily_yield >= 0 else "red"
    dy_text = f"{api_daily_yield:+,.0f} ₽"
    if api_daily_yield_relative != 0:
        dy_text += f" ({api_daily_yield_relative:+.2f}%)"
    _metric_card(c6, "Дневная доходность", dy_text, dy_color,
                 "Изменение стоимости портфеля за сегодня")

    # Доходность с момента покупки
    exp_yield_color = "green" if api_expected_yield_pct >= 0 else "red"
    _metric_card(c7, "Доходность (с покупки)", f"{api_expected_yield_pct:+.2f}%", exp_yield_color,
                 "Совокупная доходность портфеля с момента покупки")

    # DTS: тем ниже — тем лучше
    if efficiency:
        dts_val = efficiency.get('portfolio_dts', 0)
        dts_color = "green" if dts_val < 5 else ("orange" if dts_val < 15 else "red")
        _metric_card(c8, "Portfolio DTS", f"{dts_val:.1f}", dts_color,
                     "Duration × Spread (лет·%). DTS < 3 — низкий риск (ОФЗ/короткие бонды), 3–10 — умеренный, 10–25 — высокий (длинные корп. облигации), > 25 — экстремальный (высокодоходные/длинные)")
    else:
        _metric_card(c8, "Portfolio DTS", "—", "white",
                     "Duration × Spread (лет·%). DTS < 3 — низкий риск, 3–10 — умеренный, > 10 — высокий")

    # Row 3: Risk metrics
    c9, c10, c11, c12 = st.columns(4)
    if efficiency:
        eff_n = efficiency.get('effective_num_bonds', 0)
        eff_color = "green" if eff_n >= 5 else ("orange" if eff_n >= 2 else "red")
        _metric_card(c9, "Эфф. N (диверсификация)", f"{eff_n:.1f}", eff_color,
                     "Эффективное количество облигаций. Чем выше — тем лучше диверсификация")

        sharpe_val = efficiency.get('sharpe_like_ratio', 0)
        sharpe_color = "green" if sharpe_val >= 1.0 else ("orange" if sharpe_val >= 0 else "red")
        _metric_card(c10, "Sharpe-like", f"{sharpe_val:.2f}", sharpe_color,
                     "Отношение доходности к волатильности. >1 — хорошо, >2 — отлично")
    else:
        _metric_card(c9, "Эфф. N (диверсификация)", "—", "white",
                     "Эффективное количество облигаций")
        _metric_card(c10, "Sharpe-like", "—", "white",
                     "Отношение доходности к волатильности")

    total_nkd = sum(r.get("nkd", 0) * r["quantity"] for r in records)
    _metric_card(c11, "Суммарный НКД", f"{total_nkd:,.0f} ₽", "white",
                 "Накопленный купонный доход. Выплачивается при покупке/продаже")

    # Convexity: положительная = хорошо
    valid_conv = [r for r in records if (r["calc_results"].get("convexity") or 0) > 0]
    if valid_conv:
        w_conv = sum((r["calc_results"].get("convexity") or 0) * r["current_value"] for r in valid_conv)
        conv_val = w_conv / bonds_value_valid if bonds_value_valid > 0 else 0
        conv_color = "green" if conv_val > 0 else "red"
        _metric_card(c12, "Portfolio Convexity", f"{conv_val:.1f}", conv_color,
                     "Выпуклость портфеля (convexity). Мин ~0.1 (короткие бонды), макс ~10+ (длинные ОФЗ). Положительная = при росте ставок цена падает медленнее, чем предсказывает дюрация")
    else:
        _metric_card(c12, "Portfolio Convexity", "—", "white",
                     "Взвешенная выпуклость портфеля. Мин ~0.1, макс ~10+. Положительная = хорошо")

    # ══════════════════════════════════════════════════════════════════════════
    # 2. ТАБЛИЦА ПОЗИЦИЙ
    # ══════════════════════════════════════════════════════════════════════════
    st.subheader("Все позиции")

    rows = []
    for r in records:
        bd = r["bond_data"]
        cr = r["calc_results"]
        rec = r["recommendation"]
        rows.append({
            "ISIN": r["isin"],
            "Название": r["name"],
            "Кол-во": r["quantity"],
            "Цена (%)": bd.get("current_price", 0),
            "Текущая стоимость (₽)": r["current_value"],
            "P&L (₽)": r["unrealized_pnl"],
            "Дневн. (₽)": r.get("daily_pnl", 0),
            "НКД (₽)": r.get("nkd", 0) * r["quantity"],
            "YTM": bd.get("yield_moex", 0),
            "Эфф. YTM": cr.get("effective_ytm", {}).get("ytm_pct", 0),
            "Дюрация": cr.get("modified_duration_years", 0),
            "Сигнал": rec.get("signal", "N/A"),
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df.style.map(
            lambda v: "color: green" if v == "ПОКУПАТЬ"
            else "color: red" if v == "ПРОДАВАТЬ"
            else "color: orange" if v == "ДЕРЖАТЬ" else "",
            subset=["Сигнал"],
        ).map(
            lambda v: "color: green" if isinstance(v, (int, float)) and v > 0
            else "color: red" if isinstance(v, (int, float)) and v < 0 else "",
            subset=["P&L (₽)", "Дневн. (₽)"],
        ),
        use_container_width=True,
        hide_index=True,
    )

    # ══════════════════════════════════════════════════════════════════════════
    # 3. ДЕТАЛЬНЫЙ АНАЛИЗ ПО КАЖДОЙ ОБЛИГАЦИИ
    # ══════════════════════════════════════════════════════════════════════════
    st.subheader("Детальный анализ по бумагам")

    for idx, r in enumerate(records):
        bd = r["bond_data"]
        cr = r["calc_results"]
        sr = r["scenario_results"]
        forecast = r["scenario_forecast"]
        rec = r["recommendation"]

        signal = rec.get("signal", "N/A")
        if signal == "ПОКУПАТЬ":
            tag = "🟢"
        elif signal == "ПРОДАВАТЬ":
            tag = "🔴"
        else:
            tag = "🟡"

        ytm_val = bd.get("yield_moex") or 0
        with st.expander(
            f"{tag} {r['name']} — ISIN: {r['isin']} — YTM: {ytm_val:.1f}% — "
            f"Сигнал: {signal} — P&L: {r['unrealized_pnl']:+,.0f} ₽",
            expanded=False,
        ):
            # Метрики
            _render_metric_cards(bd, cr)

            # Основная информация
            st.markdown("---")
            _render_basic_info(bd)

            # Информация из портфеля
            st.markdown("---")
            st.subheader("Позиция в портфеле")
            pc1, pc2, pc3, pc4 = st.columns(4)
            pc1.metric("Количество", f"{r['quantity']:.0f} шт.")
            pc2.metric("Стоимость покупки", f"{r['cost_basis']:,.0f} ₽",
                       help="Сколько заплачено за эту позицию (цена покупки × количество)")
            pc3.metric("Текущая стоимость", f"{r['current_value']:,.0f} ₽",
                       help="Текущая рыночная стоимость позиции")
            pnl_color = "normal" if r['unrealized_pnl'] >= 0 else "inverse"
            pc4.metric("P&L", f"{r['unrealized_pnl']:+,.0f} ₽",
                       delta=f"{r['unrealized_pnl_pct']:+.1f}%", delta_color=pnl_color,
                       help="Прибыль/убыток по позиции: текущая стоимость − стоимость покупки")

            st.markdown(
                f"**Вес в портфеле:** {r['weight_pct']:.1f}% | "
                f"**Средняя цена покупки:** {r['avg_buy_price']:.2f} ₽ | "
                f"**Текущая цена:** {bd.get('current_price', 0):.2f}%"
            )

            # Расширенные метрики
            st.markdown("---")
            _render_extended_metrics(bd, cr)

            # Сценарный анализ облигации
            st.markdown("---")
            st.subheader("Сценарный анализ этой облигации")
            st.markdown(
                "Прогноз того, что произойдёт с ценой при разных изменениях ключевой ставки ЦБ. "
                "Каждый сценарий — это гипотетическая ситуация."
            )
            _render_scenario_table(sr)

            if forecast and forecast.get("expected_return_pct") is not None:
                st.markdown("**Итоговый прогноз:**")
                fc1, fc2, fc3 = st.columns(3)
                fc1.metric("Ожидаемая доходность", f"{forecast.get('expected_return_pct', 0):.2f}%",
                           help="Средняя ожидаемая доходность по всем сценариям")
                fc2.metric("Стандартное отклонение", f"{forecast.get('std_dev_pct', 0):.2f}%",
                           help="Разброс возможных исходов. Чем выше — тем менее предсказуем результат")
                fc3.metric("Кол-во сценариев", f"{forecast.get('num_scenarios', 0)}")

            # Рекомендация
            st.markdown("---")
            _render_recommendation(rec)

    # ══════════════════════════════════════════════════════════════════════════
    # 4. СЦЕНАРНЫЙ АНАЛИЗ ПОРТФЕЛЯ ЦЕЛИКОМ
    # ══════════════════════════════════════════════════════════════════════════
    if portfolio_scenarios:
        st.subheader("Сценарный анализ всего портфеля")
        st.caption(
            "Прогноз прибыли/убытка всего портфеля при разных изменениях ключевой ставки ЦБ. "
            "Помогает понять: если ставка вырастет на 2% — сколько вы потеряете, а если упадёт — сколько заработаете."
        )

        from scenario_analysis import ScenarioAnalyzer as SA
        rows_port = []
        for key, s in portfolio_scenarios.items():
            rows_port.append({
                "Сценарий": SA._scenario_display_name(key),
                "P&L портфеля (₽)": s.get("total_pnl", 0),
                "Изм. стоимости (%)": s.get("portfolio_value_change_pct", 0),
                "Худшая бумага": s.get("worst_bond", "—"),
                "Лучшая бумага": s.get("best_bond", "—"),
            })

        df_port = pd.DataFrame(rows_port)
        st.dataframe(
            df_port.style.map(
                lambda v: "color: green" if isinstance(v, (int, float)) and v > 0
                else "color: red" if isinstance(v, (int, float)) and v < 0 else "",
                subset=["P&L портфеля (₽)", "Изм. стоимости (%)"],
            ),
            use_container_width=True,
            hide_index=True,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # 5. РЕКОМЕНДАЦИИ ПО ДЕЙСТВИЯМ
    # ══════════════════════════════════════════════════════════════════════════
    st.subheader("Рекомендации по действиям")

    buy_more = [r for r in records if r["recommendation"].get("signal") == "ПОКУПАТЬ"]
    topup_list = [r for r in records if r["recommendation"].get("signal") == "ДОКУПАТЬ"]
    sell_list = [r for r in records if r["recommendation"].get("signal") == "ПРОДАВАТЬ"]
    hold_list = [r for r in records if r["recommendation"].get("signal") == "ДЕРЖАТЬ"]

    if buy_more:
        st.markdown("### 🟢 Покупать")
        st.markdown("Эти облигации имеют лучшее сочетание доходности и надёжности:")
        for r in buy_more:
            bd = r["bond_data"]
            st.markdown(
                f"- **{r['name']}** (`{r['isin']}`) — YTM: {(bd.get('yield_moex') or 0):.1f}%, "
                f"цена: {(bd.get('current_price') or 0):.2f}%, дюрация: {(r['calc_results'].get('modified_duration_years') or 0):.1f} лет"
            )

    if topup_list:
        st.markdown("### 🟠 Докупить")
        st.markdown("Хорошие облигации, которые стоит докупить при ухудшении цены:")
        for r in topup_list:
            bd = r["bond_data"]
            st.markdown(
                f"- **{r['name']}** (`{r['isin']}`) — YTM: {(bd.get('yield_moex') or 0):.1f}%, "
                f"цена: {(bd.get('current_price') or 0):.2f}%, дюрация: {(r['calc_results'].get('modified_duration_years') or 0):.1f} лет"
            )

    if sell_list:
        st.markdown("### 🔴 Рассмотреть продажу")
        st.markdown("Эти облигации лучше продать или заменить на более доходные:")
        for r in sell_list:
            bd = r["bond_data"]
            st.markdown(
                f"- **{r['name']}** (`{r['isin']}`) — YTM: {(bd.get('yield_moex') or 0):.1f}%, "
                f"P&L: {r['unrealized_pnl']:+,.0f} ₽ — {r['recommendation'].get('justification', '')[:100]}"
            )

    if hold_list:
        st.markdown("### 🟡 Держать")
        st.markdown(f"Остальные {len(hold_list)} облигации можно держать — без явных сигналов к покупке или продаже.")

    if not buy_more and not topup_list and not sell_list:
        st.info("Все облигации в нейтральной зоне — нет явных сигналов к покупке или продаже.")

    # ══════════════════════════════════════════════════════════════════════════
    # 6. РАСПРЕДЕЛЕНИЯ
    # ══════════════════════════════════════════════════════════════════════════
    st.subheader("Распределения в портфеле")

    dist1, dist2 = st.columns(2)

    with dist1:
        signal_counts = pd.Series([r["recommendation"].get("signal", "N/A") for r in records]).value_counts()
        st.markdown("**По сигналам:**")
        st.bar_chart(signal_counts)

    with dist2:
        valid_for_dur = [r for r in records if (r["calc_results"].get("modified_duration_years") or 0) > 0]
        if valid_for_dur:
            dur_vals = pd.Series([r["calc_results"]["modified_duration_years"] for r in valid_for_dur])
            dur_bins = pd.cut(dur_vals, bins=[0, 1, 3, 5, 10, 30])
            dur_dist = pd.Series(dur_bins).value_counts().sort_index()
            dur_dist.index = [str(x) for x in dur_dist.index]
            st.markdown("**По дюрации:**")
            st.bar_chart(dur_dist)

    dist3, dist4 = st.columns(2)

    with dist3:
        weights = pd.Series([r["weight_pct"] for r in records], index=[r["name"][:20] for r in records])
        st.markdown("**По весу (%):**")
        st.bar_chart(weights)

    with dist4:
        signal_emojis = {"ПОКУПАТЬ": "🟢 Покупать", "ДЕРЖАТЬ": "🟡 Держать", "ПРОДАВАТЬ": "🔴 Продавать"}
        total_by_signal = {}
        for r in records:
            sig = signal_emojis.get(r["recommendation"].get("signal", ""), "—")
            total_by_signal[sig] = total_by_signal.get(sig, 0) + r["current_value"]
        if total_by_signal:
            st.markdown("**Стоимость по сигналам (₽):**")
            st.bar_chart(pd.Series(total_by_signal))

    # ══════════════════════════════════════════════════════════════════════════
    # 7. ПРОГНОЗ ДОХОДОВ ОТ КУПОНОВ
    # ══════════════════════════════════════════════════════════════════════════
    st.subheader("Прогноз купонных доходов")
    st.caption(
        "Оценка будущих купонных выплат по портфелю. Помогает понять сколько «пассивного дохода» "
        "приносят облигации помимо роста/падения цены."
    )

    coupon_records = []
    for r in records:
        bd = r["bond_data"]
        cv = bd.get("coupon_value", 0) or 0
        cf = bd.get("coupon_frequency", 0) or 0
        qty = r["quantity"]
        if cv > 0 and cf > 0 and qty > 0:
            annual_coupon = cv * cf * qty
            coupon_records.append({
                "name": r["name"],
                "isin": r["isin"],
                "coupon_value": cv,
                "coupon_frequency": cf,
                "quantity": qty,
                "annual_coupon": annual_coupon,
                "weight_pct": r["weight_pct"],
            })

    if coupon_records:
        total_annual_coupon = sum(c["annual_coupon"] for c in coupon_records)
        avg_coupon_freq = sum(c["coupon_frequency"] * c["weight_pct"] / 100 for c in coupon_records)

        cc1, cc2, cc3 = st.columns(3)
        cc1.metric("Годовой купонный доход", f"{total_annual_coupon:,.0f} ₽",
                   help="Сколько купонов вы получите за год по всем позициям")
        cc2.metric("Средняя частота", f"{avg_coupon_freq:.1f} раз/год",
                   help="Среднее количество купонных выплат в год (взвешенно по портфелю)")
        cc3.metric("Купонная доходность", f"{total_annual_coupon / display_bonds_value * 100:.2f}%" if display_bonds_value > 0 else "—",
                   help="Купоны как % от текущей стоимости портфеля — чистый купонный доход без учёта роста цены")

        df_coupons = pd.DataFrame(coupon_records).sort_values("annual_coupon", ascending=False)
        st.dataframe(
            df_coupons.rename(columns={
                "name": "Облигация",
                "isin": "ISIN",
                "coupon_value": "Купон (₽)",
                "coupon_frequency": "Раз/год",
                "quantity": "Кол-во",
                "annual_coupon": "Годовой доход (₽)",
                "weight_pct": "Вес (%)",
            }),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("Нет данных о купонах для расчёта прогноза.")

    # ══════════════════════════════════════════════════════════════════════════
    # 8. ПРОФИЛЬ ПОГАШЕНИЯ
    # ══════════════════════════════════════════════════════════════════════════
    st.subheader("Профиль погашения")
    st.caption(
        "Когда погашаются облигации и сколько капитала вернётся в каждом периоде. "
        "Помогает планировать реинвестирование: если большая часть погасится в ближайший год — нужно заранее искать новые бумаги."
    )

    maturity_data = []
    for r in records:
        bd = r["bond_data"]
        days = bd.get("days_to_maturity", 0) or 0
        if days > 0:
            maturity_data.append({
                "name": r["name"],
                "isin": r["isin"],
                "days": days,
                "years": days / 365.0,
                "current_value": r["current_value"],
                "weight_pct": r["weight_pct"],
                "maturity_date": bd.get("maturity_date", ""),
            })

    if maturity_data:
        df_mat = pd.DataFrame(maturity_data).sort_values("days")
        total_mat_value = df_mat["current_value"].sum()

        # Bucket by time periods
        buckets = {
            "До 1 года": (0, 365),
            "1-3 года": (365, 1095),
            "3-5 лет": (1095, 1825),
            "5-10 лет": (1825, 3650),
            "Более 10 лет": (3650, 99999),
        }
        bucket_values = {}
        for label, (d_min, d_max) in buckets.items():
            val = sum(r["current_value"] for r in maturity_data if d_min < r["days"] <= d_max)
            if val > 0:
                bucket_values[label] = val

        mc1, mc2 = st.columns(2)
        with mc1:
            st.markdown("**Освобождение капитала по срокам:**")
            st.bar_chart(pd.Series(bucket_values))

        with mc2:
            avg_dur_bucket = sum(r["years"] * r["current_value"] for r in maturity_data) / total_mat_value if total_mat_value > 0 else 0
            st.metric("Средний срок до погашения (взвешенный)", f"{avg_dur_bucket:.1f} лет")

            soonest = min(maturity_data, key=lambda x: x["days"])
            latest = max(maturity_data, key=lambda x: x["days"])
            st.markdown(f"**Ближайшее погашение:** {soonest['name']} — {soonest['days']} дн.")
            st.markdown(f"**Дальнейшее погашение:** {latest['name']} — {latest['days']} дн. ({latest['years']:.1f} лет)")

        # Detailed table
        st.dataframe(
            df_mat.rename(columns={
                "name": "Облигация",
                "isin": "ISIN",
                "days": "Дней",
                "years": "Лет",
                "current_value": "Стоимость (₽)",
                "weight_pct": "Вес (%)",
                "maturity_date": "Дата погашения",
            }),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("Нет данных о сроках погашения.")

    # ══════════════════════════════════════════════════════════════════════════
    # 9. КОНЦЕНТРАЦИЯ РИСКОВ
    # ══════════════════════════════════════════════════════════════════════════
    st.subheader("Анализ концентрации")
    st.caption(
        "Насколько равномерно распределён портфель. Крупные позиции увеличивают риск: "
        "если одна облигация — 40% портфеля, её падение сильно ударит по общему результату."
    )

    sorted_by_weight = sorted(records, key=lambda r: r["weight_pct"], reverse=True)
    top5 = sorted_by_weight[:5]
    top5_weight = sum(r["weight_pct"] for r in top5)

    # Effective N = 1 / Σ(w_i²) — мера диверсификации
    weight_fractions = [r["weight_pct"] / 100 for r in records]
    herfindahl = sum(w * w for w in weight_fractions)
    effective_n = 1.0 / herfindahl if herfindahl > 0 else 0

    hc1, hc2, hc3 = st.columns(3)
    hc1.metric("Топ-5 облигаций", f"{top5_weight:.1f}% портфеля",
               help="Какую долю занимают 5 самых крупных позиций. >60% — высокая концентрация")
    hc2.metric("Кол-во бумаг", f"{len(records)}")
    hc3.metric("Эффективное N", f"{effective_n:.1f}",
               help="Сколько «равноценных» облигаций в портфеле. 10 бумаг с равными весами = N=10. Одна на 90% = N≈1.1")

    if top5_weight > 60:
        st.warning(f"⚠️ Топ-5 облигаций занимают {top5_weight:.0f}% портфеля — высокая концентрация.")
    elif top5_weight > 40:
        st.info(f"Умеренная концентрация: топ-5 = {top5_weight:.0f}%")
    else:
        st.success(f"✅ Хорошая диверсификация: топ-5 = {top5_weight:.0f}%")

    st.markdown("**Топ-5 позиций:**")
    for i, r in enumerate(top5, 1):
        bd = r["bond_data"]
        ytm_val = bd.get("yield_moex") or 0
        st.markdown(
            f"{i}. **{r['name']}** (`{r['isin']}`) — "
            f"вес: {r['weight_pct']:.1f}% ({r['current_value']:,.0f} ₽), "
            f"YTM: {ytm_val:.1f}%, "
            f"P&L: {r['unrealized_pnl']:+,.0f} ₽"
        )

    # Concentration by issuer type (emitter)
    emitter_map = {}
    for r in records:
        bd = r["bond_data"]
        emitter = bd.get("emitter", "")
        if not emitter:
            name = r["name"]
            if "ОФЗ" in name:
                emitter = "ОФЗ (Федеральные)"
            elif "ПИК" in name:
                emitter = "ПИК"
            elif "ВЭБ" in name or "ВЭБ.РФ" in name:
                emitter = "ВЭБ.РФ"
            elif "Сбер" in name:
                emitter = "Сбербанк"
            elif "Газпром" in name:
                emitter = "Газпром"
            elif "Роснефть" in name:
                emitter = "Роснефть"
            elif "ТСС" in name or "Татнфтсн" in name:
                emitter = "Татнефть"
            elif "ЛСР" in name:
                emitter = "ЛСР"
            elif "АГ" in name:
                emitter = "Апельсин Группа"
            else:
                emitter = name[:30]
        emitter_map.setdefault(emitter, 0)
        emitter_map[emitter] += r["current_value"]

    if len(emitter_map) > 1:
        emitter_total = sum(emitter_map.values())
        emitter_series = pd.Series({k: v / emitter_total * 100 for k, v in sorted(emitter_map.items(), key=lambda x: -x[1])})
        st.markdown("**Распределение по эмитентам (%):**")
        st.bar_chart(emitter_series)

    # ══════════════════════════════════════════════════════════════════════════
    # 10. ДИСКЛЕЙМЕР
    # ══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.caption(
        "⚠️ Данные получены с MOEX в реальном времени. Расчёты основаны на стандартных "
        "финансовых формулах. Это НЕ является индивидуальной инвестиционной рекомендацией. "
        "Прошлые доходности не гарантируют будущие."
    )



def screen_config() -> None:
    """Экран: Настройки."""
    st.header("Настройки")

    cfg = _cfg()

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Параметры расчёта")
        tax_rate = st.number_input("Ставка НДФЛ", value=cfg.tax_rate, step=0.01, format="%.2f")
        commission = st.number_input("Комиссия брокера", value=cfg.broker_commission, step=0.0001, format="%.4f")
        horizon = st.number_input("Горизонт инвестора (дни)", value=cfg.investment_horizon_days, step=30)

    with c2:
        st.subheader("Веса скоринга")
        sc = cfg.scoring
        y_w = st.slider("Доходность", 0.0, 1.0, sc.yield_weight)
        c_w = st.slider("Кредит", 0.0, 1.0, sc.credit_weight)
        d_w = st.slider("Дюрация", 0.0, 1.0, sc.duration_weight)
        dts_w = st.slider("DTS", 0.0, 1.0, sc.dts_weight)
        liq_w = st.slider("Ликвидность", 0.0, 1.0, sc.liquidity_weight)
        sc_w = st.slider("Сценарии", 0.0, 1.0, sc.scenario_weight)
        t_w = st.slider("Налоги", 0.0, 1.0, sc.tax_weight)

    st.subheader("T-Invest API")
    tinv_token = st.text_input(
        "Токен API Т-Инвестиций",
        value=cfg.tinvest.token,
        type="password",
        help="Получить: https://www.tbank.ru/invest/settings/ → Токены T-Bank Invest API",
    )
    tinv_sandbox = st.checkbox("Песочница (sandbox)", value=cfg.tinvest.sandbox)

    # Account selector — only shown if token is set
    tinv_account_id = getattr(cfg.tinvest, "account_id", "")
    if tinv_token:
        try:
            tmp_client = TInvestClient(
                TInvestConfig(token=tinv_token, sandbox=tinv_sandbox),
                verify_ssl=False,
            )
            broker_accounts = tmp_client.get_broker_accounts()
            if broker_accounts:
                account_options = {f"{a['name']} ({a['id']})": a['id'] for a in broker_accounts}
                account_labels = list(account_options.keys())
                # Find current selection
                current_idx = 0
                for i, a in enumerate(broker_accounts):
                    if a['id'] == getattr(cfg.tinvest, "account_id", ""):
                        current_idx = i
                        break
                selected_label = st.selectbox(
                    "Счёт T-Invest",
                    account_labels,
                    index=current_idx,
                    help="Выберите счёт для анализа портфеля",
                )
                tinv_account_id = account_options[selected_label]
            else:
                st.info("Не удалось загрузить список счетов. Проверьте токен.")
        except Exception:
            pass

    if st.button("Применить", type="primary"):
        cfg.tax_rate = tax_rate
        cfg.broker_commission = commission
        cfg.investment_horizon_days = int(horizon)
        cfg.scoring.yield_weight = y_w
        cfg.scoring.credit_weight = c_w
        cfg.scoring.duration_weight = d_w
        cfg.scoring.dts_weight = dts_w
        cfg.scoring.liquidity_weight = liq_w
        cfg.scoring.scenario_weight = sc_w
        cfg.scoring.tax_weight = t_w
        cfg.tinvest.token = tinv_token
        cfg.tinvest.sandbox = tinv_sandbox
        setattr(cfg.tinvest, "account_id", tinv_account_id)
        # Re-create T-Invest client
        st.session_state.tinvest = TInvestClient(
            TInvestConfig(token=tinv_token, sandbox=tinv_sandbox),
            verify_ssl=False,
        )
        # Persist to YAML so token survives Streamlit restart
        try:
            import yaml as _yaml
            config_path = Path(__file__).parent / "config.yaml"
            config_data = {}
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    config_data = _yaml.safe_load(f) or {}
            config_data.setdefault("tinvest", {})
            config_data["tinvest"]["token"] = tinv_token
            config_data["tinvest"]["sandbox"] = tinv_sandbox
            config_data["tinvest"]["account_id"] = tinv_account_id
            with open(config_path, "w", encoding="utf-8") as f:
                _yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)
        except Exception:
            pass
        st.session_state.pop("portfolio_api_cache", None)
        st.success("Настройки применены и сохранены в config.yaml!")


# ── Точка входа ───────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(
        page_title="MOEX Bonds Analyzer",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    _init_session()

    # Sidebar
    with st.sidebar:
        st.title("📈 MOEX Bonds")
        st.caption("Анализ облигаций РФ")
        st.divider()

        page = st.radio(
            "Раздел",
            [
                "📋 Анализ по ISIN",
                "📈 Кривая доходности",
                "🎲 Сценарный анализ",
                "🏆 Топ-рекомендации",
                "📊 Анализ портфеля",
                "⚙️ Настройки",
            ],
            label_visibility="collapsed",
        )

        st.divider()
        st.markdown(
            f"🔑 Ключевая ставка ЦБ: **{_cfg().risk_free_rate or '—'}%**\n"
            f"💰 НДФЛ: **{_cfg().tax_rate:.0%}**\n"
            f"📉 Комиссия: **{_cfg().broker_commission:.2%}**"
        )

    # Маршрутизация
    if page == "📋 Анализ по ISIN":
        screen_isin_analysis()
    elif page == "📈 Кривая доходности":
        screen_yield_curve()
    elif page == "🎲 Сценарный анализ":
        screen_scenario()
    elif page == "🏆 Топ-рекомендации":
        screen_top()
    elif page == "📊 Анализ портфеля":
        screen_portfolio()
    elif page == "⚙️ Настройки":
        screen_config()


if __name__ == "__main__":
    main()
