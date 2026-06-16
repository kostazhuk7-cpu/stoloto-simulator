import json
from pathlib import Path
from collections import Counter

games = [
    ('6x45', 'Спортлото 6 из 45', 6),
    ('5x36', 'Спортлото 5 из 36', 5),
    ('7x49', 'Спортлото 7 из 49', 7),
    ('4x20', 'Спортлото 4 из 20', 4),
    ('big', 'Большое Спортлото', 5),
    ('rapido', 'Рапидо', 8),
    ('oxota', 'Охота', 4),
    ('keno', 'КЕНО', 10),
    ('zabava', 'Забава', 12),
    ('4x20mini', '4x20 Мини', 4),
    ('6x45plus', '6x45 Плюс', 6),
    ('rapido-pro', 'Рапидо Про', 8),
    ('rapido-start', 'Рапидо Старт', 8),
    ('duel', 'Дуэль', 2),
    ('oxota-vyzov', 'Охота Вызов', 2),
]

print('=' * 80)
print('ANALIZ REAL DRAWS — PREDICTION BY FREQUENCY')
print('(based on historical data — NOT a prediction)')
print('=' * 80)

for key, name, pick_count in games:
    f = Path(f'real-draws-{key}.json')
    if not f.exists():
        continue
    
    data = json.loads(f.read_text(encoding='utf-8'))
    draws = data.get('draws', [])
    if not draws:
        continue
    
    total = len(draws)
    all_nums = []
    
    for d in draws:
        if 'field1' in d:
            all_nums.extend(d['field1'])
            all_nums.extend(d.get('field2', []))
        else:
            all_nums.extend(d.get('numbers', []))
    
    freq = Counter(all_nums)
    total_picks = len(all_nums)
    
    # Top N most frequent = prediction
    top = sorted(freq.items(), key=lambda x: -x[1])
    hot = top[:pick_count]
    cold = sorted(freq.items(), key=lambda x: x[1])[:pick_count]
    
    # Last 50 draws gap analysis
    last50_all = []
    for d in draws[-50:]:
        if 'field1' in d:
            last50_all.extend(d['field1'])
            last50_all.extend(d.get('field2', []))
        else:
            last50_all.extend(d.get('numbers', []))
    
    last50_freq = Counter(last50_all)
    
    print(f'\n{"="*80}')
    print(f'{name} ({total} тиражей, {len(freq)} чисел в диапазоне)')
    print(f'Общий расклад: {total_picks} чисел собрано, ~{total_picks/len(freq):.1f} раз на число')
    
    print(f'\n  PREDICTION (top {pick_count} frequent numbers):')
    picks = [str(n) for n, c in hot[:pick_count]]
    print(f'  -> {", ".join(picks)}')
    for n, c in hot[:8]:
        bar = '#' * min(30, int(c / max(freq.values()) * 30))
        print(f'    #{n}: {c}x {bar}')
    
    print(f'\n  COLD numbers (rarest {pick_count}):')
    cold_nums = [str(n) for n, c in cold[:pick_count]]
    print(f'  -> {", ".join(cold_nums)}')
    for n, c in cold[:6]:
        bar = '#' * max(1, int(c / max(freq.values()) * 30))
        print(f'    #{n}: {c}x {bar}')
    
    # Gap analysis (longest gap)
    if total >= 50:
        gaps = {}
        max_val = max(freq.keys())
        min_val = min(freq.keys())
        for n in range(min_val, max_val + 1):
            found = False
            for i, d in enumerate(reversed(draws[-100:])):
                nums = []
                if 'field1' in d:
                    nums = d['field1'] + d.get('field2', [])
                else:
                    nums = d.get('numbers', [])
                if n in nums:
                    gaps[n] = i
                    found = True
                    break
            if not found:
                gaps[n] = min(100, total)
        
        if gaps:
            print(f'\n  LONGEST GAP (top-5):')
            for n in sorted(gaps, key=lambda x: -gaps[x])[:5]:
                print(f'    #{n}: {gaps[n]} тиражей не было')

    # Recent performance (last 50 draws)
    if total >= 50:
        top50 = sorted(last50_freq.items(), key=lambda x: -x[1])[:pick_count]
        print(f'\n  LAST 50 DRAWS (hot):')
        recent_picks = [str(n) for n, c in top50]
        print(f'  -> {", ".join(recent_picks)}')
        for n, c in top50[:6]:
            bar = '*' * c
            print(f'    #{n}: {c}x {bar}')

print(f'\n{"="*80}')
print('IMPORTANT: Lottery is a random process.')
print('Past results do NOT affect future draws.')
print('Every number combination has the same probability.')
print('This analysis is for educational purposes only.')
print('=' * 80)
