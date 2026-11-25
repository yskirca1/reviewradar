"""tools/ai/aday/<kategori>.json -> data/<kategori>.json

  python3 tools/ai/uygula.py <kategori> [--metin]

- Analiz edilen ürünlerde sikayet ve olumlu oranları Claude etiketlerinden gelir; alıntılar yenilenir
  (şikâyet konusu başına en çok 2, olumlu konu başına en çok 1; olumlu alıntı yalnızca 4-5 yıldızlı yorumdan;
  en az 5 kelime, 90 karaktere en yakın olanlar önce).
- Ham dosyası olmayan ürünlere dokunulmaz (eski sözlük oranları kalır, yontem alanı yazılmaz).
- --metin: dikkat, ders ve öneri metinleri Claude ile yeni rakamlara göre güncellenir. Metindeki her yüzde
  veriden hesaplanan bir değerle eşleşmek zorunda; eşleşmeyen metin yazılmaz, eskisi kalır ve listelenir.
Anahtar: ANTHROPIC_API_KEY.
"""
import argparse, json, re, sys
from pathlib import Path

KOK = Path(__file__).resolve().parents[2]
AI = KOK / 'tools' / 'ai'
MODEL = 'claude-sonnet-5-5'


def kisa(m, n=160):
    m = ' '.join(m.split())
    return m if len(m) <= n and len(m.split()) >= 5 else None


def iyi(a):
    # Okunur ve açıklayıcı örnek: 5 kelimeden kısa olmasın, 60-120 karaktere en yakın olan önce
    return abs(len(a['metin']) - 90)


def alintilar(res, tem_ids, olk_ids):
    out, gor = [], set()
    neg = sorted(res['neg']['alinti'], key=iyi)
    for k in sorted(tem_ids, key=lambda k: -res['neg']['oran'].get(k, 0)):
        for a in [x for x in neg if x['konu'] == k][:2]:
            if a['metin'] in gor or not kisa(a['metin']):
                continue
            gor.add(a['metin']); out.append({'konu': k, 'yildiz': a['yildiz'], 'metin': kisa(a['metin'])})
    pos = sorted([a for a in res['top']['alinti'] if (a.get('yildiz') or 0) >= 4], key=iyi)
    for k in sorted(olk_ids, key=lambda k: -res['top']['oran'].get(k, 0)):
        a = next((x for x in pos if x['konu'] == k and x['metin'] not in gor and kisa(x['metin'])), None)
        if a:
            gor.add(a['metin']); out.append({'konu': 'Olumlu: ' + k, 'yildiz': a['yildiz'], 'metin': kisa(a['metin'])})
    return out


def kategori_oranlari(d):
    P = d['urunler']
    def w(rows, k):
        n = sum(p['ornek_dusuk'] for p in rows)
        return sum((p['sikayet'].get(k) or 0) * p['ornek_dusuk'] for p in rows) / n if n else 0
    big = [p for p in P if p['segment'] != 'KOBİ']; kobi = [p for p in P if p['segment'] == 'KOBİ']
    tem = {t['id']: {'ad': t['ad'], 'tum': w(P, t['id']), 'buyuk': w(big, t['id']), 'kobi': w(kobi, t['id'])} for t in d['temalar']}
    olk = {k['id']: {'ad': k['ad'], 'tum': sum(p['olumlu'].get(k['id']) or 0 for p in P) / len(P)} for k in d['olumlu_konular']}
    return tem, olk


def izinli(d, tem, olk):
    s = set()
    for t in tem.values():
        s |= {round(t['tum'] * 100), round(t['buyuk'] * 100), round(t['kobi'] * 100)}
    for k in olk.values():
        s.add(round(k['tum'] * 100))
    for p in d['urunler']:
        s |= {round(v * 100) for v in p['sikayet'].values() if v} | {round(v * 100) for v in p['olumlu'].values() if v}
        s.add(round(100 * (p['yildiz'][0] + p['yildiz'][1]) / p['yazili']))
    y = sum(p['yazili'] for p in d['urunler']); dd = sum(p['yildiz'][0] + p['yildiz'][1] for p in d['urunler'])
    s.add(round(100 * dd / y))
    return s


def yuzdeler(t):
    return [int(x) for x in re.findall(r"%\s?(\d+)", t)]


def metinleri_guncelle(d, analiz_edilen):
    import anthropic
    tem, olk = kategori_oranlari(d)
    izin = izinli(d, tem, olk)
    ozet = {
        'kategori': d['ad'],
        'kategori_sikayet_oranlari': {t['ad']: {'tum': f"%{round(t['tum']*100)}", 'buyuk_marka': f"%{round(t['buyuk']*100)}", 'kobi': f"%{round(t['kobi']*100)}"} for t in sorted(tem.values(), key=lambda x: -x['tum'])},
        'kategori_olumlu_oranlari': {k['ad']: f"%{round(k['tum']*100)}" for k in sorted(olk.values(), key=lambda x: -x['tum'])},
        'urunler': [{
            'id': p['id'], 'ad': p['kisa'], 'segment': p['segment'],
            '1-2 yildiz payi': f"%{round(100*(p['yildiz'][0]+p['yildiz'][1])/p['yazili'])}",
            'sikayet': {tem[k]['ad']: f"%{round(v*100)}" for k, v in sorted(p['sikayet'].items(), key=lambda x: -(x[1] or 0)) if v},
            'olumlu': {olk[k]['ad']: f"%{round(v*100)}" for k, v in sorted(p['olumlu'].items(), key=lambda x: -(x[1] or 0)) if v},
            'ornek_yorumlar': [a['metin'] for a in p['alinti']][:8],
            'eski_dikkat': p['dikkat'], 'eski_ders': p['ders']} for p in d['urunler']],
        'eski_oneriler': [{'baslik': o['baslik'], 'metin': o['metin']} for o in d['oneriler']],
    }
    sistem = ('Bir pazar araştırması sitesinin metinlerini yeni ölçümlere göre güncelliyorsun. Şikâyet ve olumlu oranları yeniden ölçüldü. '
              'Eski metinleri koru; yalnızca yeni verilerle çelişen rakamları ve iddiaları düzelt. Yazım tarzı aynı kalsın: kısa, düz Türkçe cümleler. '
              'KURALLAR: (1) Metne yazdığın her yüzde, verilen verideki bir yüzdeyle birebir aynı olmalı. Yeni yüzde uydurma, toplama ya da ortalama yapma; '
              'birden fazla konunun birleşik oranını yazma, konuları ayrı ayrı ver. (2) Bir yüzde artık verideki hiçbir değerle tutmuyorsa onu sil ya da veriden doğru değeri koy. '
              '(3) Tırnak içindeki müşteri sözleri değişmesin. (4) Her ürünün dikkat ve ders madde sayısı eskisiyle aynı kalsın; öneri sayısı ve sırası aynı kalsın, başlıkları gerekmedikçe değiştirme. '
              '(5) Cevabı yalnızca kaydet aracıyla ver.')
    arac = {'name': 'kaydet', 'description': 'Güncellenmiş metinler', 'input_schema': {'type': 'object', 'properties': {
        'urunler': {'type': 'array', 'items': {'type': 'object', 'properties': {'id': {'type': 'string'}, 'dikkat': {'type': 'array', 'items': {'type': 'string'}}, 'ders': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['id', 'dikkat', 'ders']}},
        'oneriler': {'type': 'array', 'items': {'type': 'object', 'properties': {'baslik': {'type': 'string'}, 'metin': {'type': 'string'}}, 'required': ['baslik', 'metin']}}},
        'required': ['urunler', 'oneriler']}}
    r = anthropic.Anthropic().messages.create(model=MODEL, max_tokens=16000, system=sistem, tools=[arac], tool_choice={'type': 'auto'},
                                              messages=[{'role': 'user', 'content': json.dumps(ozet, ensure_ascii=False)}])
    blok = next((x for x in r.content if x.type == 'tool_use'), None)
    if not blok:
        sys.exit('Model metin döndürmedi')
    yeni = blok.input
    if isinstance(yeni.get('urunler'), str): yeni['urunler'] = json.loads(yeni['urunler'])
    if isinstance(yeni.get('oneriler'), str): yeni['oneriler'] = json.loads(yeni['oneriler'])
    red = []
    by = {u['id']: u for u in yeni['urunler']}
    for p in d['urunler']:
        u = by.get(p['id'])
        if not u:
            red.append((p['kisa'], 'ürün dönmedi')); continue
        for alan in ('dikkat', 'ders'):
            if len(u[alan]) != len(p[alan]):
                red.append((p['kisa'], f'{alan} madde sayısı değişti')); continue
            kotu = [x for t in u[alan] for x in yuzdeler(t) if x not in izin]
            if kotu:
                red.append((p['kisa'], f'{alan}: veride olmayan yüzde {kotu}')); continue
            p[alan] = u[alan]
    if len(yeni['oneriler']) == len(d['oneriler']):
        for o, n in zip(d['oneriler'], yeni['oneriler']):
            kotu = [x for x in yuzdeler(n['metin']) if x not in izin]
            if kotu:
                red.append((o['baslik'], f'öneri: veride olmayan yüzde {kotu}')); continue
            o['baslik'], o['metin'] = n['baslik'], n['metin']
    else:
        red.append(('öneriler', 'sayı değişti'))
    return red


def main():
    p = argparse.ArgumentParser(); p.add_argument('kategori'); p.add_argument('--metin', action='store_true'); a = p.parse_args()
    yol = KOK / 'data' / f'{a.kategori}.json'
    d = json.loads(yol.read_text(encoding='utf-8'))
    ad = json.loads((AI / 'aday' / f'{a.kategori}.json').read_text(encoding='utf-8'))
    tem_ids = [t['id'] for t in d['temalar']]; olk_ids = [k['id'] for k in d['olumlu_konular']]
    n = 0
    for u in d['urunler']:
        res = ad['urunler'].get(u['id'])
        if not res:
            continue
        u['sikayet'] = {k: res['neg']['oran'].get(k, 0) for k in tem_ids}
        u['olumlu'] = {k: res['top']['oran'].get(k, 0) for k in olk_ids}
        if res['neg']['n'] != u['ornek_dusuk']:
            print(f"  not: {u['kisa']} ornek_dusuk {u['ornek_dusuk']} -> {res['neg']['n']}")
            u['ornek_dusuk'] = res['neg']['n']
        u['alinti'] = alintilar(res, tem_ids, olk_ids)
        u['yontem'] = 'claude'
        n += 1
    d['yontem'] = 'claude'
    print(f"{d['ad']}: {n}/{len(d['urunler'])} ürün güncellendi, {sum(len(u['alinti']) for u in d['urunler'])} alıntı")
    if a.metin:
        red = metinleri_guncelle(d, n)
        print('Metin güncellemesinde reddedilenler (eski metin kaldı):', red or 'yok')
    yol.write_text(json.dumps(d, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
