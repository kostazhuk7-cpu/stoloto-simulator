import json
f = 'real-draws-keno.json'
d = json.loads(open(f, encoding='utf-8').read())
for draw in d['draws']:
    if len(draw.get('numbers',[])) > 20:
        draw['numbers'] = draw['numbers'][:20]
d['note'] = 'KENO: 20 drawn numbers per draw'
open(f,'w',encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=2))
print(f'Fixed keno: {d["totalDraws"]} draws, sample: {d["draws"][0]}')
