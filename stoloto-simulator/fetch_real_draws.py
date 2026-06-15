"""
Парсер реальных тиражей stoloto.ru для всех 11 игр.
Использует публичный POST-эндпоинт /draw-results/{slug}/load (без логина).
"""
import requests
import json
import re
import time
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup

BASE = "https://www.stoloto.ru"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
OUT_DIR = Path(__file__).parent

# Sizes for double-field games: (field1_size, field2_size)
DOUBLE_SIZES = {"4x20": (4,4), "oxota": (4,4), "5x2": (5,2)}

# Конфигурация игр: slug → (gameKey, format)
GAMES = {
    "6x45":       ("6x45",       "single"),
    "5x36plus":   ("5x36",       "rapido"),
    "7x49":       ("7x49",       "single"),
    "4x20":       ("4x20",       "double"),
    "5x2":        ("big",        "double"),
    "12x24":      ("allornothing","single"),
    "rapido2":    ("rapido",     "rapido"),
    "oxota":      ("oxota",      "double"),
    "keno":       ("keno",       "single"),
    "zodiac":     ("zodiac",     "single"),
}


def fetch_page(slug, date_from, date_to, page):
    """POST запрос к /draw-results/{slug}/load, возвращает HTML данных."""
    url = f"{BASE}/draw-results/{slug}/load"
    resp = requests.post(url, data={
        "from": date_from,
        "to": date_to,
        "page": page,
        "mode": "date"
    }, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"API error: {data}")
    return data["data"], data.get("stop", False)


def parse_single(html):
    """Парсит одно поле чисел (6x45, 7x49, 12x24, keno, zodiac)."""
    soup = BeautifulSoup(html, "html.parser")
    draws = []
    for elem in soup.select(".elem"):
        try:
            draw_link = elem.select_one(".draw a")
            if not draw_link:
                continue
            draw_num = int(draw_link.text.strip())
            numbers = [int(b.text.strip()) for b in elem.select(".numbers b, .numbers .container .numbers b")]
            if not numbers:
                # Альтернативный селектор для некоторых игр
                nums_el = elem.select_one(".numbers, .container")
                if nums_el:
                    numbers = [int(t.strip()) for t in re.findall(r'\b(\d+)\b', nums_el.get_text())]
            if numbers:
                draws.append({"draw": draw_num, "numbers": numbers})
        except (ValueError, AttributeError):
            continue
    return draws


def parse_double(html, split_sizes=None):
    """Парсит два поля (4x20, oxota, big Спортлото).
    split_sizes: (size1, size2) — как делить числа на поля. По умолчанию пополам."""
    if split_sizes is None:
        split_sizes = (4, 4)
    soup = BeautifulSoup(html, "html.parser")
    draws = []
    for elem in soup.select(".elem"):
        try:
            draw_link = elem.select_one(".draw a")
            if not draw_link:
                continue
            draw_num = int(draw_link.text.strip())
            containers = elem.select(".container")
            all_numbers = []
            for c in containers:
                nums = [int(b.text.strip()) for b in c.select("b") if b.text.strip().isdigit()]
                all_numbers.extend(nums)
            if not all_numbers:
                all_numbers = [int(b.text.strip()) for b in elem.select(".numbers b") if b.text.strip().isdigit()]
            if all_numbers:
                s1, s2 = split_sizes
                field1 = all_numbers[:s1]
                field2 = all_numbers[s1:s1+s2]
                draws.append({"draw": draw_num, "field1": field1, "field2": field2})
        except (ValueError, AttributeError):
            continue
    return draws


def parse_rapido(html):
    """Парсит Рапидо/5x36: числа + бонус."""
    soup = BeautifulSoup(html, "html.parser")
    draws = []
    for elem in soup.select(".elem"):
        try:
            draw_link = elem.select_one(".draw a")
            if not draw_link:
                continue
            draw_num = int(draw_link.text.strip())
            all_numbers = [int(b.text.strip()) for b in elem.select(".numbers b")
                           if b.text.strip().isdigit()]
            if len(all_numbers) >= 2:
                # Последнее число — бонус
                bonus = all_numbers[-1]
                numbers = all_numbers[:-1]
                draws.append({"draw": draw_num, "numbers": numbers, "bonus": bonus})
            elif all_numbers:
                draws.append({"draw": draw_num, "numbers": all_numbers})
        except (ValueError, AttributeError):
            continue
    return draws


def collect_game(slug, game_key, fmt):
    """Собирает все доступные тиражи для одной игры."""
    print(f"\n{'='*60}")
    print(f"Сбор: {game_key} (slug={slug}, формат={fmt})")
    print(f"{'='*60}")

    all_draws = []
    page = 2  # Начинаем со страницы 2 (1 — главная, её данные уже в page=1? Нет, page=1 = пустая)
    max_pages = 10
    date_from = "01.01.2024"
    date_to = datetime.now().strftime("%d.%m.%Y")

    while page <= max_pages:
        try:
            html, stopped = fetch_page(slug, date_from, date_to, page)
        except Exception as e:
            print(f"  [ОШИБКА] page={page}: {e}")
            break

        if fmt == "single":
            draws = parse_single(html)
        elif fmt == "double":
            draws = parse_double(html)
        elif fmt == "rapido":
            draws = parse_rapido(html)
        else:
            draws = parse_single(html)

        if not draws:
            print(f"  page={page}: 0 тиражей (останов)")
            break

        all_draws.extend(draws)
        print(f"  page={page}: +{len(draws)} тиражей (всего: {len(all_draws)})")

        if stopped or len(draws) < 20:
            break
        page += 1
        time.sleep(0.5)

    # Сортируем по номеру тиража и убираем дубликаты
    seen = set()
    unique = []
    for d in sorted(all_draws, key=lambda x: x["draw"]):
        if d["draw"] not in seen:
            seen.add(d["draw"])
            unique.append(d)

    # Ограничиваем до 200 последних тиражей
    unique = unique[-200:]

    # Сохраняем
    filename = f"real-draws-{game_key}.json"
    data = {
        "game": game_key,
        "source": "stoloto.ru API",
        "fetchedAt": datetime.now().strftime("%Y-%m-%d"),
        "totalDraws": len(unique),
        "draws": unique
    }
    outpath = OUT_DIR / filename
    outpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> Saved: {filename} ({len(unique)} draws)")

    return unique


def collect_game_direct(slug, game_key, fmt):
    """Собирает тиражи для игр с серверным рендерингом (12x24, keno, zodiac)."""
    print(f"\n{'='*60}")
    print(f"Direct fetch: {game_key} (slug={slug})")

    url = f"{BASE}/{slug}/archive"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    html = resp.text

    if fmt == "single":
        draws = parse_single(html)
    elif fmt == "double":
        draws = parse_double(html)
    elif fmt == "rapido":
        draws = parse_rapido(html)
    else:
        draws = parse_single(html)

    seen = set()
    unique = []
    for d in sorted(draws, key=lambda x: x["draw"]):
        if d["draw"] not in seen:
            seen.add(d["draw"])
            unique.append(d)

    unique = unique[-200:]
    filename = f"real-draws-{game_key}.json"
    data = {
        "game": game_key,
        "source": "stoloto.ru direct HTML",
        "fetchedAt": datetime.now().strftime("%Y-%m-%d"),
        "totalDraws": len(unique),
        "draws": unique
    }
    outpath = OUT_DIR / filename
    outpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> Saved: {filename} ({len(unique)} draws)")
    return unique


def analyze_hot_numbers():
    """Анализирует самые частые числа за последние 100 тиражей."""
    print(f"\n{'='*60}")
    print("HOT NUMBERS (last 100 draws)")
    print(f"{'='*60}")

    for slug, (game_key, fmt) in GAMES.items():
        filename = OUT_DIR / f"real-draws-{game_key}.json"
        if not filename.exists():
            continue
        data = json.loads(filename.read_text(encoding="utf-8"))
        draws = data.get("draws", [])[-100:]  # последние 100

        freq = {}
        for d in draws:
            if fmt == "double":
                nums = d.get("field1", []) + d.get("field2", [])
            else:
                nums = d.get("numbers", [])
            for n in nums:
                freq[n] = freq.get(n, 0) + 1

        if not freq:
            continue
        top = sorted(freq.items(), key=lambda x: -x[1])[:8]
        print(f"\n{game_key} ({len(draws)} draws):")
        for num, count in top:
            bar = "#" * count
            print(f"  {num:>3}: {count:>3} {bar}")


if __name__ == "__main__":
    # Игры с POST-эндпоинтом
    post_games = {k: v for k, v in GAMES.items() if k not in ("12x24", "keno", "zodiac")}
    # Игры с прямым HTML (серверный рендеринг)
    direct_games = {k: GAMES[k] for k in ("12x24", "keno", "zodiac")}

    total_new = 0
    for slug, (game_key, fmt) in post_games.items():
        try:
            draws = collect_game(slug, game_key, fmt)
            total_new += len(draws)
        except Exception as e:
            print(f"  [ERROR] {game_key}: {e}")

    for slug, (game_key, fmt) in direct_games.items():
        try:
            draws = collect_game_direct(slug, game_key, fmt)
            total_new += len(draws)
        except Exception as e:
            print(f"  [ERROR] {game_key}: {e}")

    print(f"\n{'='*60}")
    print(f"TOTAL: {total_new} draws across {len(GAMES)} games")
    print(f"{'='*60}")

    analyze_hot_numbers()
