import json
from collections import Counter

data = json.loads(open('real-draws-5x36.json', encoding='utf-8').read())
draws = data['draws']
total = len(draws)

main_nums = [n for d in draws for n in d.get('numbers', [])]
bonus_nums = [d.get('bonus') for d in draws if d.get('bonus') is not None]

freq_main = Counter(main_nums)

# Last 20 draws
last20 = draws[-20:]
last20_main = [n for d in last20 for n in d.get('numbers', [])]
last20_freq = Counter(last20_main)

# Gap analysis
gaps = {}
for n in range(1, 37):
    found = False
    for i, d in enumerate(reversed(draws)):
        if n in d.get('numbers', []):
            gaps[n] = i + 1
            found = True
            break
    if not found:
        gaps[n] = total

max_freq = max(freq_main.values()) if freq_main else 1

print('=== Спортлото 5 из 36 — Статистика ===')
print(f'Всего тиражей: {total}')
print(f'Чисел в базе: {len(main_nums)}')
print(f'Ожидание: ~{len(main_nums)/36:.1f} раз на число при равномерном')
print()

print('--- Частые числа (топ-10) ---')
for n, c in freq_main.most_common(10):
    bar = '#' * int(c / max_freq * 20)
    print(f'  {n:>2}: {c:>4}x {bar}')

print()
print('--- Редкие числа (топ-10) ---')
for n, c in list(reversed(sorted(freq_main.items(), key=lambda x: x[1])))[:10]:
    bar = '#' * max(1, int(c / max_freq * 20))
    print(f'  {n:>2}: {c:>4}x {bar}')

print()
print('--- Последние 15 тиражей ---')
for d in reversed(last20[-15:]):
    nums = d.get('numbers', [])
    b = d.get('bonus', '')
    print(f"  #{d['draw']}: {nums} бонус={b}")

print()
print('--- Числа НЕ выпадавшие дольше всего ---')
for n in sorted(gaps, key=lambda x: -gaps[x])[:6]:
    print(f'  Число {n}: пропустило {gaps[n]} тиражей')

print()
print('--- Числа выпадавшие недавно ---')
for n in sorted(gaps, key=lambda x: gaps[x])[:6]:
    print(f'  Число {n}: {gaps[n]} тиражей назад')

print()
print('--- Частые в последних 20 тиражах ---')
for n, c in last20_freq.most_common(8):
    bar = '*' * c
    print(f'  Число {n}: {c}x {bar}')

print()
print('========================================')
print('ВАЖНО: Каждый тираж независим.')
print('Вероятность числа 11 в следующем тираже: 5/36 = 13.9%')
print('Вероятность числа 22 в следующем тираже: 5/36 = 13.9%')
print('Они равны. Прошлые результаты не влияют.')
print('========================================')
