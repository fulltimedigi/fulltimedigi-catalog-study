#!/usr/bin/env python3
"""
Catalogue study — collector (PROTOCOL.md §4, §6).

For each store: find its product URLs, draw a fixed-seed random sample of five,
render each page, save it raw, and score the store with measure.py.

Politeness: one store at a time per worker, >=2s between requests to the same
host, at most 8 requests per store, robots.txt respected.

Usage:
  python3 collect.py --stores stores.tsv --out /path/out --platform zid [--limit 50] [--workers 6]
"""
import argparse, json, os, random, re, subprocess, sys, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure import measure_store

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
SEED = 20260915
N_PAGES = 5
PROD_RE = re.compile(r'/p\d{6,}|/products/|/product/|/ar/[^/]+/p\d+')

def chrome():
    import glob
    c = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    return c[-1]

def spki():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'proxy-spki.txt')
    return open(p).read().strip() if os.path.exists(p) else ''

def curl(url, timeout=25):
    p = subprocess.run(['curl', '-sS', '-L', '-m', str(timeout), '-A', UA, url],
                       capture_output=True)
    return p.stdout.decode('utf-8', 'ignore') if p.returncode == 0 else ''

def render(url, timeout=70):
    args = [chrome(), '--headless=new', '--no-sandbox', '--disable-gpu',
            f'--user-agent={UA}', '--virtual-time-budget=20000', '--dump-dom', url]
    s = spki()
    if s:
        args.insert(4, f'--ignore-certificate-errors-spki-list={s}')
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout)
        return p.stdout.decode('utf-8', 'ignore')
    except subprocess.TimeoutExpired:
        return ''

class Robots:
    """Minimal, correct robots.txt reader.

    Python's urllib robotparser returns a spurious "disallow" on real
    storefront robots files (it mis-parses the long bot blocks these
    platforms ship), so the rules are read directly instead. Only the
    `User-agent: *` group is consulted, and the longest matching rule wins,
    as the standard specifies.
    """
    def __init__(self, base):
        self.rules = []
        body = curl(urllib.parse.urljoin(base, '/robots.txt'), timeout=15)
        star, seen_star = False, False
        for raw in body.splitlines():
            line = raw.split('#', 1)[0].strip()
            if not line or ':' not in line:
                continue
            k, v = line.split(':', 1)
            k, v = k.strip().lower(), v.strip()
            if k == 'user-agent':
                if seen_star and star:
                    star = False          # the * group ended
                star = star or (v == '*')
                seen_star = seen_star or (v == '*')
            elif star and k in ('allow', 'disallow') and v:
                self.rules.append((v, k == 'allow'))

    @staticmethod
    def _rx(pattern):
        """robots.txt pattern to regex: * is any run, a trailing $ anchors the end."""
        end = pattern.endswith('$')
        body = pattern[:-1] if end else pattern
        rx = ''.join('.*' if ch == '*' else re.escape(ch) for ch in body)
        return re.compile('^' + rx + ('$' if end else ''))

    def can_fetch(self, url):
        path = urllib.parse.urlparse(url).path or '/'
        best, verdict = -1, True
        for pattern, allow in self.rules:
            if self._rx(pattern).match(path) and len(pattern) > best:
                best, verdict = len(pattern), allow
        return verdict

NON_PRODUCT = re.compile(
    r'/p/|/page-\d|/c\d{4,}|/brand-|/blog|/tag/|/category|/collections/?$|'
    r'/cart|/login|/account|\.xml$|/sitemap', re.I)

def _sitemap_urls(base):
    """Every URL in the store's sitemaps, blog sitemaps excluded."""
    sm = curl(urllib.parse.urljoin(base, '/sitemap.xml'))
    locs = re.findall(r'<loc>([^<]+)</loc>', sm)
    subs = [s for s in locs if s.endswith('.xml') and 'blog' not in s.lower()]
    if not subs:
        return locs
    urls = []
    for s in subs[:8]:
        urls += re.findall(r'<loc>([^<]+)</loc>', curl(s))
        time.sleep(2)
        if len(urls) > 4000:
            break
    return urls

def _salla_path_store(base):
    """salla.sa/<slug> storefronts refuse their sitemap (403), so product links
    are read from the rendered pages: home gives the categories, a category
    gives the products."""
    root = base.rstrip('/')
    home = render(root)
    cats = sorted(set(re.findall(re.escape(root) + r'/[^"\s]*?/c\d{6,}', home)))
    urls = re.findall(re.escape(root) + r'/[^"\s]*?/p\d{6,}', home)
    for c in cats[:3]:
        time.sleep(2)
        urls += re.findall(re.escape(root) + r'/[^"\s]*?/p\d{6,}', render(c))
        if len(urls) > 40:
            break
    return urls

def product_urls(base):
    """Product URLs for one store. Shopify exposes them directly; other
    platforms are read from the sitemap, with non-product shapes removed
    rather than product shapes guessed, since storefronts use several
    different product URL patterns (/p<id>, /products/<handle>, short codes)."""
    parsed = urllib.parse.urlparse(base)
    host = parsed.netloc
    urls = []
    pj = curl(urllib.parse.urljoin(base, '/products.json?limit=250'))
    if pj.strip().startswith('{'):
        try:
            for p in json.loads(pj).get('products', []):
                urls.append(urllib.parse.urljoin(base, '/products/' + p['handle']))
        except Exception:
            pass
    if len(urls) < 10 and host == 'salla.sa' and parsed.path.strip('/'):
        urls = _salla_path_store(base)
    if len(urls) < 10:
        # Last resort: read product links straight off the rendered home page.
        # Some storefronts publish no usable sitemap yet link their products
        # from the front page.
        home = render(base.rstrip('/'))
        urls = re.findall(r'https?://[^"\s]*?' + re.escape(host) + r'/[^"\s]*?(?:/p\d{6,}|/products/[^"\s?#]+)', home)
        urls = [u for u in urls if not NON_PRODUCT.search(u)]
    if len(urls) < 10:
        raw = _sitemap_urls(base)
        cands = [u for u in raw if host in u and not NON_PRODUCT.search(u)]
        strong = [u for u in cands if re.search(r'/p\d{6,}|/products/|/product/', u)]
        urls = strong or [u for u in cands if u.rstrip('/') != base.rstrip('/')]
    seen, out = set(), []
    for u in urls:
        if host in u and u not in seen:
            seen.add(u); out.append(u)
    return out

def do_store(base, outdir):
    rec = {'store': base, 'status': 'ok'}
    try:
        rp = Robots(base)
        urls = product_urls(base)
        rec['products_found'] = len(urls)
        if len(urls) < 10:
            rec['status'] = 'excluded_few_products'; return rec
        ar = [u for u in urls if '/en/' not in u] or urls
        random.Random(SEED).shuffle(ar)
        picked, pages, kept = ar[:N_PAGES], [], []
        sd = os.path.join(outdir, 'raw', re.sub(r'[^a-zA-Z0-9.-]', '_', urllib.parse.urlparse(base).netloc))
        os.makedirs(sd, exist_ok=True)
        for i, u in enumerate(picked, 1):
            if not rp.can_fetch(u):
                continue
            h = render(u)
            if len(h) < 5000:
                continue
            open(os.path.join(sd, f'p{i}.html'), 'w', encoding='utf-8').write(h)
            pages.append(h); kept.append(u)
            time.sleep(2)
        rec['pages_rendered'] = len(pages)
        if len(pages) < 3:
            rec['status'] = 'excluded_render_failed'; return rec
        rec['rows'] = measure_store(pages, kept)
    except Exception as e:
        rec['status'] = 'error'; rec['error'] = str(e)[:200]
    return rec

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stores', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--platform', required=True); ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--workers', type=int, default=6)
    a = ap.parse_args()
    os.makedirs(os.path.join(a.out, 'raw'), exist_ok=True)
    stores = []
    with open(a.stores, encoding='utf-8') as f:
        for i, line in enumerate(f):
            if i == 0 and line.lower().startswith('url'):
                continue
            u = line.split('\t')[0].strip()
            if u.startswith('http'):
                stores.append(u)
    if a.limit:
        stores = stores[:a.limit]
    outf = os.path.join(a.out, f'measurements_{a.platform}.jsonl')
    done = set()
    if os.path.exists(outf):
        for l in open(outf, encoding='utf-8'):
            try: done.add(json.loads(l)['store'])
            except Exception: pass
    todo = [s for s in stores if s not in done]
    print(f'{a.platform}: {len(stores)} stores, {len(done)} already done, {len(todo)} to do', flush=True)
    with open(outf, 'a', encoding='utf-8') as out, ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(do_store, s, a.out): s for s in todo}
        for k, fu in enumerate(as_completed(futs), 1):
            rec = fu.result(); rec['platform'] = a.platform
            out.write(json.dumps(rec, ensure_ascii=False) + '\n'); out.flush()
            if k % 10 == 0:
                print(f'  {k}/{len(todo)}', flush=True)
    print('done', flush=True)

if __name__ == '__main__':
    main()
