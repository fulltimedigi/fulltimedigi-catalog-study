#!/usr/bin/env python3
"""
Catalogue study — product page measurement (PROTOCOL.md §5).

Theme-independent method. A store's five sampled product pages are measured
together: any text line or image that repeats across most of them is site
chrome (header, nav, footer, policy links, logos) and is subtracted. What is
left is that page's own product content. This avoids per-theme selectors,
which do not survive across hundreds of different storefront templates.

Every field is True (present/adequate), False (missing/inadequate) or
None (UNMEASURABLE — counted in neither direction, reported separately).

Usage:
  python3 measure.py --store <url> --pages p1.html p2.html ... [--urls u1 u2 ...]
"""
import argparse, json, re

CUR = r'(?:ر\.?\s?س|ريال|SAR)'
AR = re.compile(r'[؀-ۿ]')
BOILERPLATE_RATIO = 0.6          # a line seen on >=60% of the store's pages is chrome

def _clean(h):
    return re.sub(r'<(script|style|noscript|template)[^>]*>.*?</\1>', ' ', h, flags=re.S | re.I)

def lines_of(h):
    """Visible text split into lines, preserving block boundaries."""
    x = _clean(h)
    x = re.sub(r'<(br|/p|/div|/li|/h[1-6]|/section|/td|/tr)\s*/?>', '\n', x, flags=re.I)
    x = re.sub(r'<[^>]+>', ' ', x)
    x = re.sub(r'&nbsp;?', ' ', x)
    out = []
    for ln in x.split('\n'):
        ln = re.sub(r'\s+', ' ', ln).strip()
        if ln:
            out.append(ln)
    return out

def images_of(h):
    srcs = re.findall(r'<img[^>]+src="([^"]+)"', _clean(h), re.I)
    srcs += re.findall(r'<source[^>]+srcset="([^",\s]+)', _clean(h), re.I)
    keep = set()
    for s in srcs:
        if s.startswith('data:') or re.search(r'\.svg($|\?)', s, re.I):
            continue
        keep.add(re.sub(r'[?#].*$', '', s))
    return keep

def jsonld(h):
    out = []
    for b in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', h, re.S | re.I):
        try:
            d = json.loads(b.strip())
        except Exception:
            continue
        for item in (d if isinstance(d, list) else [d]):
            if isinstance(item, dict):
                out.extend(g for g in item.get('@graph', []) if isinstance(g, dict))
                out.append(item)
    return out

def product_ld(h):
    for d in jsonld(h):
        t = d.get('@type')
        if t == 'Product' or (isinstance(t, list) and 'Product' in t):
            return d
    return None

def measure_store(pages, urls=None):
    """pages: list of raw HTML strings for one store. Returns one row per page."""
    urls = urls or [''] * len(pages)
    n = len(pages)
    line_sets = [lines_of(h) for h in pages]
    img_sets = [images_of(h) for h in pages]

    from collections import Counter
    lc = Counter()
    for ls in line_sets:
        lc.update(set(ls))
    ic = Counter()
    for s in img_sets:
        ic.update(s)
    cut = max(2, int(n * BOILERPLATE_RATIO))
    chrome_lines = {l for l, c in lc.items() if c >= cut}
    chrome_imgs = {i for i, c in ic.items() if c >= cut}

    rows = []
    for h, ls, ims, u in zip(pages, line_sets, img_sets, urls):
        own_lines = [l for l in ls if l not in chrome_lines]
        own_text = ' '.join(own_lines)
        own_imgs = ims - chrome_imgs
        ld = product_ld(h) or {}
        r = {'url': u, 'has_product_ld': bool(ld)}

        # 1 description: prose unique to this page. A JSON-LD description is used
        # only when it is not a truncated meta description.
        d = str(ld.get('description') or '')
        if d and not d.endswith(('...', '…')):
            words = len(d.split())
        else:
            prose = [l for l in own_lines if len(AR.findall(l)) >= 15]
            words = len(' '.join(prose).split())
        r['desc_words'] = words
        r['f1_description'] = words >= 40

        # 2 images unique to this page
        r['image_count'] = len(own_imgs)
        r['f2_images'] = None if not ims else (len(own_imgs) >= 2)

        # 3 options
        r['f3_options'] = bool(re.search(
            r'(اختر|اختار|حدد)\s*(ال)?(مقاس|حجم|لون|نوع|تركيز|سعة|الخيار)|'
            r'data-option-id|product-option|s-product-options|variant-', h, re.I))

        # 4 category / breadcrumb
        r['f4_category'] = any(x.get('@type') == 'BreadcrumbList' for x in jsonld(h)) or \
            bool(re.search(r'breadcrumb|BreadcrumbList', h, re.I))

        offers = ld.get('offers') if isinstance(ld.get('offers'), dict) else {}
        # 5 price
        # Price. Storefront themes often render the amount and the currency
        # symbol as separate elements, so adjacency cannot be required: a
        # currency token plus a price-shaped number in the page's own content
        # is enough, and structured data is preferred when present.
        has_cur = bool(re.search(CUR + r'|﷼', own_text)) or bool(re.search(CUR + r'|﷼', h))
        has_amount = bool(re.search(r'\d+[.,]\d{2}\b|\b\d{2,6}\b', own_text))
        r['f5_price'] = bool(offers.get('price')) or \
            bool(re.search(r'"price"\s*:\s*"?\d', h)) or (has_cur and has_amount)
        # 6 availability
        r['f6_availability'] = bool(offers.get('availability')) or \
            bool(re.search(r'(أضف|اضف|إضافة|اضافة)\s*(إلى|الى|ل)?\s*السلة|'
                           r'أضف للسلة|شراء الآن|اشتر الآن|نفدت|نفذت|غير متوفر|متوفر|'
                           r'add-to-cart|add_to_cart', own_text + h, re.I))
        # 7 reviews
        r['f7_reviews'] = bool(ld.get('aggregateRating') or ld.get('review')) or \
            bool(re.search(r'التقييمات|تقييم المنتج|مراجعات|آراء العملاء|rating-stars|review-stars', h, re.I))
        # 8 brand
        b = ld.get('brand')
        bname = b.get('name') if isinstance(b, dict) else (b if isinstance(b, str) else None)
        # Brand as a labelled, visible field. A "brand" key inside a theme's
        # internal JSON is not a field the shopper can see, so it is not counted.
        r['f8_brand'] = bool(bname and str(bname).strip()) or \
            bool(re.search(r'الماركة|العلامة التجارية|الشركة المصنعة|الموديل', own_text))
        # 9 shipping stated in the product content (not the site-wide footer)
        r['f9_shipping'] = bool(re.search(
            r'(الشحن|التوصيل|شحن|توصيل)[^\n]{0,60}?(مجان|' + CUR + r'|\d+\s*(يوم|أيام|ساعة|ساعات)|خلال)', own_text))
        # 10 returns stated in the product content
        r['f10_returns'] = bool(re.search(r'الإرجاع|الاسترجاع|الاستبدال|إرجاع|استبدال|استرجاع', own_text))

        fs = [k for k in r if k.startswith('f')]
        r['measured'] = sum(1 for k in fs if r[k] is not None)
        r['missing'] = sum(1 for k in fs if r[k] is False)
        r['unmeasurable'] = sum(1 for k in fs if r[k] is None)
        r['own_words'] = len(own_text.split())
        rows.append(r)
    return rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--store', default='')
    ap.add_argument('--pages', nargs='+', required=True)
    ap.add_argument('--urls', nargs='*', default=None)
    a = ap.parse_args()
    pages = [open(p, encoding='utf-8', errors='ignore').read() for p in a.pages]
    for row in measure_store(pages, a.urls):
        row['store'] = a.store
        print(json.dumps(row, ensure_ascii=False))

if __name__ == '__main__':
    main()
