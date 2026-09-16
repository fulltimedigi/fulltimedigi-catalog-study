#!/usr/bin/env python3
"""Build the release dataset: only the fields the reliability check kept."""
import json, os, csv, statistics, collections

R = os.path.expanduser('~/fulltimedigi-research/catalog-study-2026')
OUT = './data'
os.makedirs(OUT, exist_ok=True)

AGREE = json.load(open(f'{R}/reliability/agreement.json'))
FIELD = {'f1_description':'description','f5_price':'price','f6_availability':'availability',
         'f7_reviews':'reviews','f8_brand':'brand','f2_images':'images','f3_options':'options',
         'f4_category':'category','f9_shipping':'shipping_returns','f10_returns':'shipping_returns'}
KEPT = [f for f, name in FIELD.items() if AGREE[name]['keep']]
DROPPED = [f for f in FIELD if f not in KEPT]
REPORTED = [f for f in KEPT if f not in ('f5_price', 'f6_availability')]
print('kept   :', KEPT)
print('columns:', REPORTED)
print('dropped:', DROPPED)

rows = []
for p in ('salla', 'shopify', 'zid'):
    for line in open(f'{R}/measurements_{p}.jsonl', encoding='utf-8'):
        if line.strip():
            r = json.loads(line); r['platform'] = p; rows.append(r)

stores, pages = [], []
for r in rows:
    if r['status'] != 'ok' or not r['rows']:
        continue
    valid = []
    for pg in r['rows']:
        # A page counts as a product page only if the two structural markers are present.
        if not (pg.get('f5_price') and pg.get('f6_availability')):
            continue
        rec = {'store': r['store'], 'platform': r['platform'], 'url': pg['url'],
               'has_product_ld': pg['has_product_ld'], 'desc_words': pg['desc_words'],
               'own_words': pg['own_words']}
        # price and availability are the inclusion criterion above, so they are true for every
        # released page by construction. Publishing them as columns would invite a 100% reading.
        for f in REPORTED:
            rec[f[3:]] = pg.get(f)
        rec['measured_fields'] = sum(1 for f in REPORTED if pg.get(f) is not None)
        rec['missing_fields'] = sum(1 for f in REPORTED if pg.get(f) is False)
        valid.append(rec)
    if not valid:
        continue
    pages.extend(valid)
    dw = [v['desc_words'] for v in valid]
    stores.append({
        'store': r['store'], 'platform': r['platform'],
        'products_found': r['products_found'], 'pages_measured': len(valid),
        'median_desc_words': statistics.median(dw),
        'pages_under_40_words': sum(1 for w in dw if w < 40),
        'brand_missing': sum(1 for v in valid if v.get('brand') is False),
        'reviews_missing': sum(1 for v in valid if v.get('reviews') is False),
    })

for name, data in (('pages', pages), ('stores', stores)):
    with open(f'{OUT}/{name}.csv', 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(data[0].keys())); w.writeheader(); w.writerows(data)
    with open(f'{OUT}/{name}.jsonl', 'w', encoding='utf-8') as fh:
        for d in data: fh.write(json.dumps(d, ensure_ascii=False) + '\n')

dw = [p['desc_words'] for p in pages]
summary = {
    'stores_collected': len(rows),
    'stores_measured': sum(1 for r in rows if r['status'] == 'ok'),
    'stores_in_release': len(stores),
    'pages_measured': sum(len(r['rows']) for r in rows if r['status'] == 'ok'),
    'pages_in_release': len(pages),
    'by_platform': dict(collections.Counter(s['platform'] for s in stores)),
    'fields_kept': [f[3:] for f in KEPT],
    'fields_as_columns': [f[3:] for f in REPORTED],
    'inclusion_criterion': 'price and availability both present on the page',
    'fields_dropped': sorted({FIELD[f] for f in DROPPED}),
    'median_desc_words': statistics.median(dw),
    'pages_under_40_words_pct': round(100 * sum(1 for w in dw if w < 40) / len(dw), 1),
    'pages_under_10_words_pct': round(100 * sum(1 for w in dw if w < 10) / len(dw), 1),
    'brand_missing_pct': round(100 * sum(1 for p in pages if p.get('brand') is False) / len(pages), 1),
    'reviews_missing_pct': round(100 * sum(1 for p in pages if p.get('reviews') is False) / len(pages), 1),
    'median_desc_words_by_platform': {
        pl: statistics.median([p['desc_words'] for p in pages if p['platform'] == pl])
        for pl in ('salla', 'shopify', 'zid')},
}
json.dump(summary, open(f'{OUT}/summary.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print(json.dumps(summary, ensure_ascii=False, indent=2))
