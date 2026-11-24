"""ReviewRadar: yorumları Claude API ile konulara ayırır (regex sözlüğünün yerine).

Akış (kategori başına):
  python3 tools/ai/siniflandir.py gonder <kategori> --ham <rv_*.json klasörü>   # Batch API'ye gönderir
  python3 tools/ai/siniflandir.py durum  <kategori>                             # batch bitti mi?
  python3 tools/ai/siniflandir.py topla  <kategori>                             # etiketleri indirir
  python3 tools/ai/siniflandir.py rapor  <kategori>                             # oranlar + eski/yeni karşılaştırma

Kurallar
- Konu listesi data/<kategori>.json'daki temalar ve olumlu_konular'dan gelir; yeni konu uydurulmaz.
- Paydalar sitedekiyle aynı: şikâyet oranı = 1-2 yıldızlı okunan yorumlar (neg), olumlu oran = önerilen ilk 100 (top).
- Alıntı yalnızca yorumun kendi metninden birebir alınır; kodda kontrol edilir, birebir değilse atılır.
- data/ dosyalarına yazmaz. Aday çıktı tools/ai/aday/<kategori>.json'a yazılır; siteye geçiş ayrı ve onaylı adımdır.
Anahtar: ANTHROPIC_API_KEY ortam değişkeni.
"""
import argparse, glob, json, os, sys, time
from pathlib import Path

KOK = Path(__file__).resolve().parents[2]
AI = KOK / 'tools' / 'ai'
MODEL = os.environ.get('RR_MODEL', 'claude-sonnet-5-5')
PARCA = 25  # istek başına yorum


def kategori(kat):
    return json.loads((KOK / 'data' / f'{kat}.json').read_text(encoding='utf-8'))


def ham_oku(kat, ham_dir):
    d = kategori(kat)
    ham, eksik = {}, []
    for u in d['urunler']:
        yollar = glob.glob(os.path.join(ham_dir, '**', f"rv_{u['id']}.json"), recursive=True)
        if yollar:
            ham[u['id']] = json.loads(Path(yollar[0]).read_text(encoding='utf-8'))
        else:
            eksik.append(f"{u['id']} {u['kisa']}")
    return d, ham, eksik


def sistem(d, tur):
    if tur == 'neg':
        konular = [(t['id'], t['ad']) for t in d['temalar']]
        gorev = ('Bunlar 1-2 yıldızlı yorumlar. Her yorumda geçen ŞİKÂYET konularını işaretle. '
                 'Bir yorum birden çok konuya girebilir. Hiçbir konuya girmiyorsa boş liste ver ve '
                 '"diger" alanına 2-5 kelimeyle ne dediğini yaz (örn. "övgü, puan yanlış", "henüz kullanmadım", "kargo gecikmesi").')
    else:
        konular = [(t['id'], t['ad']) for t in d['olumlu_konular']]
        gorev = ('Bunlar Trendyol\'un "Önerilen" sıralamasındaki yorumlar. Her yorumda geçen OLUMLU konuları işaretle '
                 '(müşterinin övdüğü, memnun kaldığı şeyler). Yalnızca açıkça söyleneni işaretle; şikâyet cümlelerini olumlu sayma.')
    liste = '\n'.join(f'- {i}: {a}' for i, a in konular)
    return (f"Sen bir Türk e-ticaret yorum analistisin. Kategori: {d['ad']}.\n{gorev}\n\nKonular (yalnızca bu kimlikleri kullan):\n{liste}\n\n"
            'Kurallar: Konu, yorumda gerçekten anlatılıyorsa işaretlenir; tek bir kelimenin geçmesi yetmez '
            '(örn. "fiyatına göre iyi" bir fiyat şikâyeti değildir). Emin değilsen işaretleme. '
            '"alinti" alanına, işaretlediğin konuyu en iyi gösteren, yorumdan BİREBİR kopyalanmış 4-20 kelimelik bir parça yaz; '
            'yazım hatalarını düzeltme, kelime ekleme. Konu yoksa alinti null olsun. Cevabını YALNIZCA etiketle aracını bir kez çağırarak, listedeki her yorum için bir öğeyle ver.')


def arac(d, tur):
    ids = [t['id'] for t in (d['temalar'] if tur == 'neg' else d['olumlu_konular'])]
    oge = {'type': 'object', 'properties': {
        'i': {'type': 'integer'},
        'konular': {'type': 'array', 'items': {'type': 'string', 'enum': ids}},
        'alinti': {'type': ['string', 'null']},
        'diger': {'type': ['string', 'null']}}, 'required': ['i', 'konular', 'alinti']}
    return {'name': 'etiketle', 'description': 'Her yorum için etiketleri kaydet.',
            'input_schema': {'type': 'object', 'properties': {'etiketler': {'type': 'array', 'items': oge}}, 'required': ['etiketler']}}


def istekler(d, ham):
    out = []
    for uid, h in ham.items():
        for tur in ('neg', 'top'):
            yorumlar = [r for r in h.get(tur, []) if (r.get('text') or '').strip()]
            for bas in range(0, len(yorumlar), PARCA):
                parca = yorumlar[bas:bas + PARCA]
                metin = '\n\n'.join(f"[{bas + j}] ({r.get('stars')}★) {r['text']}" for j, r in enumerate(parca))
                out.append({'custom_id': f'{uid}-{tur}-{bas}', 'params': {
                    'model': MODEL, 'max_tokens': 4000,
                    'system': [{'type': 'text', 'text': sistem(d, tur), 'cache_control': {'type': 'ephemeral'}}],
                    'tools': [arac(d, tur)], 'tool_choice': {'type': 'auto'},
                    'messages': [{'role': 'user', 'content': f'Ürün: {h.get("meta", {}).get("title", uid)}\n\n{metin}'}]}})
    return out


def is_yolu(kat):
    (AI / 'is').mkdir(parents=True, exist_ok=True)
    return AI / 'is' / f'{kat}.json'


def gonder(a):
    import anthropic
    d, ham, eksik = ham_oku(a.kategori, a.ham)
    if eksik:
        print('Ham dosyası bulunamayan ürünler (bunlar analize girmez):\n  ' + '\n  '.join(eksik))
    if not ham:
        sys.exit('Hiç ham dosya bulunamadı.')
    ist = istekler(d, ham)
    n = sum(len(h.get('neg', [])) + len(h.get('top', [])) for h in ham.values())
    print(f'{len(ham)} ürün, {n} yorum, {len(ist)} istek, model {MODEL}')
    if a.kuru:
        print(json.dumps(ist[0], ensure_ascii=False, indent=1)[:3000]); return
    b = anthropic.Anthropic().messages.batches.create(requests=ist)
    is_yolu(a.kategori).write_text(json.dumps({'batch': b.id, 'model': MODEL, 'ham': os.path.abspath(a.ham),
                                               'eksik': eksik, 'zaman': time.strftime('%Y-%m-%d %H:%M')}, ensure_ascii=False))
    print('Gönderildi:', b.id)


def durum(a):
    import anthropic
    j = json.loads(is_yolu(a.kategori).read_text())
    b = anthropic.Anthropic().messages.batches.retrieve(j['batch'])
    print(b.processing_status, b.request_counts)


def topla(a):
    import anthropic
    j = json.loads(is_yolu(a.kategori).read_text())
    c = anthropic.Anthropic()
    sonuc, hata = {}, []
    for r in c.messages.batches.results(j['batch']):
        if r.result.type != 'succeeded':
            hata.append(r.custom_id); continue
        blok = next((x for x in r.result.message.content if x.type == 'tool_use'), None)
        if not blok:
            hata.append(r.custom_id); continue
        sonuc[r.custom_id] = blok.input.get('etiketler', [])
    (AI / 'etiket').mkdir(exist_ok=True)
    (AI / 'etiket' / f'{a.kategori}.json').write_text(json.dumps(sonuc, ensure_ascii=False), encoding='utf-8')
    print(f'{len(sonuc)} istek alındı, {len(hata)} hata', hata[:10])


def norm(s):
    return ' '.join((s or '').replace('I', 'ı').replace('İ', 'i').lower().split())


def rapor(a):
    j = json.loads(is_yolu(a.kategori).read_text())
    d, ham, _ = ham_oku(a.kategori, j['ham'])
    et = json.loads((AI / 'etiket' / f'{a.kategori}.json').read_text(encoding='utf-8'))
    tem = {t['id']: t['ad'] for t in d['temalar']}
    olk = {t['id']: t['ad'] for t in d['olumlu_konular']}
    urun_ai, satirlar = {}, []
    toplam_neg = toplam_konusuz = toplam_alinti_red = 0
    digerler = {}
    for u in d['urunler']:
        h = ham.get(u['id'])
        if not h:
            continue
        lab = {'neg': {}, 'top': {}}
        for cid, liste in et.items():
            uid, tur, _ = cid.rsplit('-', 2)
            if uid != u['id']:
                continue
            if isinstance(liste, str):
                try: liste = json.loads(liste)
                except Exception: liste = []
            for e in liste:
                if isinstance(e, dict) and isinstance(e.get('i'), int):
                    lab[tur][e['i']] = e
        res = {}
        for tur, konu_ad in (('neg', tem), ('top', olk)):
            yorumlar = [r for r in h.get(tur, []) if (r.get('text') or '').strip()]
            n = len(yorumlar)
            say = {k: 0 for k in konu_ad}
            alintilar = []
            for i, r in enumerate(yorumlar):
                e = lab[tur].get(i)
                if not e:
                    continue
                ks = [k for k in e.get('konular', []) if k in say]
                for k in ks:
                    say[k] += 1
                if tur == 'neg':
                    toplam_neg += 1
                    if not ks:
                        toplam_konusuz += 1
                        dg = (e.get('diger') or '?').strip().lower()
                        digerler[dg] = digerler.get(dg, 0) + 1
                al = e.get('alinti')
                if ks and al:
                    if norm(al) in norm(r['text']):
                        alintilar.append({'konu': ks[0], 'yildiz': r.get('stars'), 'metin': al.strip()})
                    else:
                        toplam_alinti_red += 1
            res[tur] = {'n': n, 'oran': {k: round(v / n, 2) if n else 0 for k, v in say.items()}, 'alinti': alintilar}
        urun_ai[u['id']] = res
        for k in tem:
            eski, yeni = u['sikayet'].get(k, 0) or 0, res['neg']['oran'][k]
            if abs(yeni - eski) >= 0.05:
                satirlar.append((u['kisa'], 'şikâyet', tem[k], eski, yeni))
        for k in olk:
            eski, yeni = u['olumlu'].get(k, 0) or 0, res['top']['oran'][k]
            if abs(yeni - eski) >= 0.05:
                satirlar.append((u['kisa'], 'olumlu', olk[k], eski, yeni))
    (AI / 'aday').mkdir(exist_ok=True)
    (AI / 'aday' / f'{a.kategori}.json').write_text(json.dumps(
        {'model': j['model'], 'batch': j['batch'], 'eksik': j['eksik'], 'urunler': urun_ai}, ensure_ascii=False, indent=1), encoding='utf-8')
    # Kategori düzeyi (sitedeki gibi ürün ortalaması değil; burada okunan yorumlar üzerinden kaba karşılaştırma)
    print(f'\n== {d["ad"]}: {len(urun_ai)} ürün, model {j["model"]} ==')
    print(f'1-2 yıldızlı yorumlarda konusuz: {toplam_konusuz}/{toplam_neg} (%{100 * toplam_konusuz / max(toplam_neg, 1):.0f})')
    print(f'Birebir olmadığı için atılan alıntı: {toplam_alinti_red}')
    print('Konusuz yorumların en sık açıklamaları:', sorted(digerler.items(), key=lambda x: -x[1])[:12])
    print(f'\n5 puandan fazla değişen oranlar ({len(satirlar)}):')
    for s in sorted(satirlar, key=lambda s: -abs(s[4] - s[3]))[:60]:
        print(f'  {s[0][:28]:28} {s[1]:8} {s[2][:38]:38} regex %{s[3] * 100:3.0f} -> Claude %{s[4] * 100:3.0f}')
    print(f'\nAday: tools/ai/aday/{a.kategori}.json')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    s = p.add_subparsers(dest='komut', required=True)
    g = s.add_parser('gonder'); g.add_argument('kategori'); g.add_argument('--ham', required=True); g.add_argument('--kuru', action='store_true')
    for k in ('durum', 'topla', 'rapor'):
        s.add_parser(k).add_argument('kategori')
    a = p.parse_args()
    {'gonder': gonder, 'durum': durum, 'topla': topla, 'rapor': rapor}[a.komut](a)
