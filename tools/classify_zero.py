#!/usr/bin/env python3
"""Classify the stores where product discovery found nothing.

A store that returns no products is not automatically a store with no
products: it may be closed for maintenance, may block automated reading, or
may really be near-empty. The protocol excludes each of these for a
different reason, so they are separated here rather than lumped together.
"""
import json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import curl, render

MAINT = re.compile(r'المتجر مغلق|قيد الصيانة|maintenance\.css|store-closed|المتجر غير متاح', re.I)
SOON  = re.compile(r'قريبا|قريباً|coming soon', re.I)

def classify(store):
    h = curl(store, timeout=25)
    if not h:
        h = render(store)
        if not h:
            return 'unreachable'
    if MAINT.search(h):
        return 'closed_or_maintenance'
    if len(h) < 8000 and SOON.search(h):
        return 'coming_soon'
    r = render(store)
    if not r or len(r) < 8000:
        return 'blocked_or_empty_render'
    if re.search(r'/p\d{6,}|/products/', r):
        return 'has_products_after_all'
    return 'live_but_no_products_found'

def main():
    D = os.path.expanduser('~/fulltimedigi-research/catalog-study-2026')
    targets = []
    for p in ['salla', 'zid', 'shopify']:
        f = f'{D}/measurements_{p}.jsonl'
        if not os.path.exists(f): continue
        for l in open(f, encoding='utf-8'):
            r = json.loads(l)
            if r.get('products_found') == 0:
                targets.append((p, r['store']))
    print(f'classifying {len(targets)} zero-product stores', flush=True)
    out = open(f'{D}/zero_product_classification.jsonl', 'w', encoding='utf-8')
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(classify, s): (p, s) for p, s in targets}
        for i, fu in enumerate(as_completed(futs), 1):
            p, s = futs[fu]
            try: k = fu.result()
            except Exception as e: k = 'error'
            out.write(json.dumps({'platform': p, 'store': s, 'class': k}, ensure_ascii=False) + '\n')
            out.flush()
            if i % 25 == 0: print(f'  {i}/{len(targets)}', flush=True)
    out.close()
    print('done', flush=True)

if __name__ == '__main__':
    main()
