"""TickBit: generates a static news-and-markets site (AI, politics, crypto, stocks)."""
import calendar, hashlib, html, json, os, re, socket, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests
from jinja2 import Environment
from markupsafe import Markup

socket.setdefaulttimeout(20)
UA = {"User-Agent": "Mozilla/5.0 (compatible; TickBit/1.0)"}
feedparser.USER_AGENT = UA["User-Agent"]
YAHOO = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

_repo = os.getenv("GITHUB_REPOSITORY", "")
_auto = f"https://{_repo.split('/')[0].lower()}.github.io/{_repo.split('/')[1]}" if "/" in _repo else "https://example.com"
SITE_URL = (os.getenv("SITE_URL") or _auto).rstrip("/")
NEWSLETTER_ACTION = os.getenv("NEWSLETTER_ACTION", "")  # form address from Buttondown / MailerLite
ADSENSE, ADSENSE_SLOT = os.getenv("ADSENSE_CLIENT", ""), os.getenv("ADSENSE_SLOT", "")
KEEP_DAYS = 7
OUT, CACHE = Path("site"), Path("data/cache.json")

# key: (menu label, page title, feeds)
TOPICS = {
    "ai": ("AI", "Artificial Intelligence", [
        "https://techcrunch.com/category/artificial-intelligence/feed/",
        "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
        "https://www.technologyreview.com/feed/",
        "https://arstechnica.com/ai/feed/",
        "https://venturebeat.com/category/ai/feed/"]),
    "politics": ("Politics", "Politics", [
        "http://feeds.bbci.co.uk/news/politics/rss.xml",
        "https://rss.politico.com/politics-news.xml",
        "https://feeds.npr.org/1014/rss.xml",
        "https://thehill.com/feed/",
        "https://www.theguardian.com/politics/rss",
        "https://www.aljazeera.com/xml/rss/all.xml"]),
    "crypto": ("Crypto", "Cryptocurrencies", [
        "https://cointelegraph.com/rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://decrypt.co/feed",
        "https://www.theblock.co/rss.xml",
        "https://bitcoinmagazine.com/.rss/full/"]),
    "stocks": ("Stocks", "Stocks", [
        "https://feeds.marketwatch.com/marketwatch/topstories/",
        "https://www.cnbc.com/id/10001147/device/rss/rss.html",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "https://finance.yahoo.com/news/rssindex",
        "http://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.theguardian.com/uk/business/rss"]),
}
SOURCES = {"techcrunch.com": "TechCrunch", "theverge.com": "The Verge", "technologyreview.com": "MIT Technology Review",
           "arstechnica.com": "Ars Technica", "venturebeat.com": "VentureBeat", "cointelegraph.com": "Cointelegraph",
           "coindesk.com": "CoinDesk", "decrypt.co": "Decrypt", "theblock.co": "The Block", "bitcoinmagazine.com": "Bitcoin Magazine",
           "marketwatch.com": "MarketWatch", "cnbc.com": "CNBC", "yahoo.com": "Yahoo Finance", "bbc.co.uk": "BBC News",
           "bbc.com": "BBC News", "theguardian.com": "The Guardian", "politico.com": "Politico", "npr.org": "NPR",
           "thehill.com": "The Hill", "aljazeera.com": "Al Jazeera"}

STABLE = {"USDT", "USDC", "USDE", "DAI", "USDS", "FDUSD", "TUSD", "BUSD"}
# Large US stocks. The 15 with the biggest live market cap are shown (this order is used if caps are unavailable).
STOCK_POOL = {"NVDA": "Nvidia", "AAPL": "Apple", "GOOGL": "Alphabet", "MSFT": "Microsoft", "AMZN": "Amazon",
              "AVGO": "Broadcom", "META": "Meta", "TSLA": "Tesla", "LLY": "Eli Lilly", "MU": "Micron",
              "WMT": "Walmart", "JPM": "JPMorgan", "AMD": "AMD", "XOM": "Exxon Mobil", "V": "Visa",
              "BRK-B": "Berkshire Hathaway", "ORCL": "Oracle", "NFLX": "Netflix", "MA": "Mastercard", "COST": "Costco",
              "JNJ": "Johnson & Johnson", "BAC": "Bank of America", "PLTR": "Palantir", "CSCO": "Cisco"}


# ---------- text helpers ----------
def clean(t):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(t or ""))).strip()


def snippet(text, title, n=210):
    t = re.sub(r"\s*The post .* appeared first on .*$", "", clean(text))
    t = re.sub(r"\s*(Continue reading|Read more)\W*$", "", t, flags=re.I)
    if not t or t.lower().startswith(title.lower()[:40]):
        return ""
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0].rstrip(",;:- ") + "…"


def source_name(link):
    host = urlparse(link).netloc.lower()
    return next((n for d, n in SOURCES.items() if host.endswith(d)), re.sub(r"^www\.", "", host))


def ago(ts):
    m = max(int((time.time() - ts) / 60), 1)
    if m < 60:
        return f"{m} min ago"
    h = m // 60
    if h < 24:
        return f"{h} hour{'s' if h > 1 else ''} ago"
    return f"{h // 24} day{'s' if h // 24 > 1 else ''} ago"


# ---------- news ----------
def fetch_feed(job):
    topic, url = job
    try:
        return [(topic, e) for e in feedparser.parse(url).entries[:12]]
    except Exception as ex:
        print("Feed error:", url, ex)
        return []


def load_items():
    cache = json.loads(CACHE.read_text("utf-8")) if CACHE.exists() else {}
    jobs = [(t, u) for t, (_, _, feeds) in TOPICS.items() for u in feeds]
    with ThreadPoolExecutor(8) as pool:
        batches = list(pool.map(fetch_feed, jobs))
    for topic, e in (x for b in batches for x in b):
        link, title = e.get("link"), clean(e.get("title"))
        if not link or not title:
            continue
        ts = calendar.timegm(e.published_parsed) if e.get("published_parsed") else time.time()
        cache[hashlib.sha1(link.encode()).hexdigest()[:16]] = {
            "topic": topic, "link": link, "ts": ts, "source": source_name(link),
            "title": title, "summary": snippet(e.get("summary"), title)}
    cutoff = time.time() - KEEP_DAYS * 86400
    cache = {k: v for k, v in cache.items() if v["ts"] > cutoff}
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), "utf-8")
    seen, out = set(), []
    for i in sorted(cache.values(), key=lambda i: -i["ts"]):
        key = re.sub(r"\W+", "", i["title"].lower())
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


def pick(items, topic, n, per_source=8):
    out, count = [], {}
    for i in items:
        if i["topic"] == topic and count.get(i["source"], 0) < per_source:
            count[i["source"]] = count.get(i["source"], 0) + 1
            out.append(i)
            if len(out) == n:
                break
    return out


# ---------- markets ----------
def spark(v, w=96, h=28):
    if len(v) < 3:
        return ""
    lo, hi = min(v), max(v)
    r = (hi - lo) or 1
    pts = " ".join(f"{i * w / (len(v) - 1):.1f},{h - 2 - (x - lo) / r * (h - 4):.1f}" for i, x in enumerate(v))
    return Markup(f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true"><polyline points="{pts}" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>')


def money(v):
    if not v:
        return "—"
    for lim, suf, d in ((1e12, "T", 2), (1e9, "B", 1), (1e6, "M", 0)):
        if v >= lim:
            return f"${v / lim:,.{d}f}{suf}"
    return f"${v:,.0f}"


def row(sym, name, price, chg, wk, series, kind, cap=None):
    p = f"{price:,.2f}" if price >= 1 else f"{price:.4f}"
    return dict(sym=sym, name=name, price=p, chg=round(chg, 2), wk=round(wk, 2), spark=spark(series), kind=kind,
                cap=cap or 0, capf=money(cap))


def crypto():
    try:  # the 15 largest cryptocurrencies by market cap
        d = requests.get("https://api.coingecko.com/api/v3/coins/markets", headers=UA, timeout=20, params={
            "vs_currency": "usd", "order": "market_cap_desc", "per_page": 15, "page": 1,
            "sparkline": "true", "price_change_percentage": "24h,7d"}).json()
        return [row(c["symbol"].upper(), c["name"], c["current_price"], c.get("price_change_percentage_24h") or 0,
                    c.get("price_change_percentage_7d_in_currency") or 0, c["sparkline_in_7d"]["price"][::4], "crypto", c.get("market_cap"))
                for c in d]
    except Exception as ex:
        print("Crypto prices unavailable:", ex)
        return []


def closes(sym):
    try:  # daily closes for ~1 month (Yahoo Finance)
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", headers=YAHOO, timeout=10,
                         params={"range": "1mo", "interval": "1d"}).json()
        cl = [c for c in r["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c]
        if len(cl) >= 6:
            return cl
    except Exception:
        pass
    try:  # fallback: today's open and close (Stooq)
        f = requests.get(f"https://stooq.com/q/l/?s={sym.lower()}.us&f=sohlc&h&e=csv", headers=UA, timeout=10).text.splitlines()[1].split(",")
        return [float(f[1]), float(f[4])]
    except Exception as ex:
        print("No price for", sym, ex)
        return []


def market_caps(symbols):
    try:  # unofficial public Yahoo endpoint; may stop working at any time
        s = requests.Session()
        s.headers.update(YAHOO)
        s.get("https://fc.yahoo.com", timeout=10)
        crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10).text
        r = s.get("https://query1.finance.yahoo.com/v7/finance/quote", timeout=15,
                  params={"symbols": ",".join(symbols), "crumb": crumb}).json()
        return {q["symbol"]: q.get("marketCap") for q in r["quoteResponse"]["result"]}
    except Exception as ex:
        print("Market caps unavailable:", ex)
        return {}


def stocks():
    with ThreadPoolExecutor(8) as pool:
        data = dict(zip(STOCK_POOL, pool.map(closes, STOCK_POOL)))
    caps = market_caps(list(STOCK_POOL))
    rows = [row(s, STOCK_POOL[s], cl[-1], (cl[-1] / cl[-2] - 1) * 100,
                (cl[-1] / cl[-6] - 1) * 100 if len(cl) >= 6 else (cl[-1] / cl[-2] - 1) * 100, cl, "stocks", caps.get(s))
            for s, cl in data.items() if len(cl) >= 2]
    if any(r["cap"] for r in rows):
        rows.sort(key=lambda r: -r["cap"])
    return rows[:15]


# ---------- page template ----------
TPL = """{% macro feed(rows) %}{% for i in rows %}<article><div class="meta">{{ i.source }} · {{ i.when }}</div>
<h3><a href="{{ i.link }}" rel="noopener nofollow" target="_blank">{{ i.title }}</a></h3>{% if i.summary %}<p>{{ i.summary }}</p>{% endif %}</article>
{% else %}<p class="empty">No stories yet. Please check back soon.</p>{% endfor %}{% endmacro %}
{% macro table(rows, label) %}<table class="mk"><caption>{{ label }}</caption><thead><tr><th class="fv"><span class="sr-only">Favorite</span></th><th>Asset</th><th class="r">Price (USD)</th><th class="r">24h</th><th class="r">Market cap</th><th class="r hide">1W</th><th class="hide">Trend</th></tr></thead><tbody>
{% for p in rows %}<tr data-s="{{ p.sym }}"><td class="fv"><button class="fav" aria-label="Add {{ p.sym }} to favorites" aria-pressed="false">☆</button></td><td><b>{{ p.sym }}</b> <span class="mute hide">{{ p.name }}</span></td><td class="r">{{ p.price }}</td>
<td class="r {{ 'u' if p.chg>=0 else 'd' }}">{{ '+' if p.chg>=0 }}{{ p.chg }}%</td><td class="r">{{ p.capf }}</td><td class="r hide {{ 'u' if p.wk>=0 else 'd' }}">{{ '+' if p.wk>=0 }}{{ p.wk }}%</td><td class="hide {{ 'u' if p.wk>=0 else 'd' }}">{{ p.spark }}</td></tr>
{% else %}<tr><td colspan="7" class="empty">Prices are temporarily unavailable.</td></tr>{% endfor %}</tbody></table>{% endmacro %}
{% macro mini(rows, label, href) %}<div class="blk"><table class="mk mini"><caption>{{ label }}</caption><thead><tr><th>Asset</th><th class="r">Price</th><th class="r">24h</th><th class="r">Mkt cap</th></tr></thead><tbody>
{% for p in rows %}<tr><td><b>{{ p.sym }}</b></td><td class="r">{{ p.price }}</td><td class="r {{ 'u' if p.chg>=0 else 'd' }}">{{ '+' if p.chg>=0 }}{{ p.chg }}%</td><td class="r">{{ p.capf }}</td></tr>
{% else %}<tr><td colspan="4" class="empty">Prices are temporarily unavailable.</td></tr>{% endfor %}</tbody></table><a class="more" href="{{ root }}{{ href }}/">See all →</a></div>{% endmacro %}
{% macro ad(n) %}<aside class="ad" aria-label="Advertisement"><small>Advertisement</small>{% if adsense and slot %}<ins class="adsbygoogle" style="display:block" data-ad-client="{{ adsense }}" data-ad-slot="{{ slot }}" data-ad-format="auto" data-full-width-responsive="true"></ins><script>(adsbygoogle=window.adsbygoogle||[]).push({});</script>{% else %}<div class="slot">Ad space {{ n }}</div>{% endif %}</aside>{% endmacro %}
<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }}</title><meta name="description" content="{{ desc }}"><link rel="canonical" href="{{ url }}">
<meta property="og:title" content="{{ title }}"><meta property="og:description" content="{{ desc }}"><meta property="og:type" content="website">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@500;700;800&family=Source+Serif+4:wght@400;600&display=swap" rel="stylesheet">
{% if adsense %}<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={{ adsense }}" crossorigin="anonymous"></script>{% endif %}
<style>
:root{--bg:#fff;--soft:#f4f6fa;--ink:#0a1020;--mute:#5a6479;--line:#e1e6ef;--acc:#3d5bff;--up:#00935f;--down:#d92d43;--gap:2.5rem;color-scheme:light}
@media(prefers-color-scheme:dark){:root{--bg:#0b1020;--soft:#121a2e;--ink:#eef1f8;--mute:#9aa5bd;--line:#232d48;--up:#2fd39a;--down:#ff6b7d;color-scheme:dark}}
body[data-k=ai]{--acc:#3d5bff}body[data-k=politics]{--acc:#9b5cff}body[data-k=crypto]{--acc:#f0a010}body[data-k=stocks]{--acc:#12a96b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:500 1rem/1.5 Manrope,system-ui,sans-serif}
a{color:inherit}:focus-visible{outline:3px solid var(--acc);outline-offset:2px}.mute{color:var(--mute);font-weight:500}.u{color:var(--up)}.d{color:var(--down)}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.w{max-width:70rem;margin:0 auto;padding:0 1.25rem}
.tape{background:var(--ink);color:var(--bg);overflow:hidden;white-space:nowrap;font-size:.72rem;font-weight:700;line-height:1.2}
.tape div{display:inline-block;padding:.35rem 0;animation:run 60s linear infinite;will-change:transform}.tape span{margin:0 .9rem}.tape .u{color:#35d6a0}.tape .d{color:#ff8a99}@keyframes run{to{transform:translateX(-50%)}}
@media(prefers-reduced-motion:reduce){.tape{overflow-x:auto}.tape div{animation:none}}
.hd{display:grid;gap:.75rem;padding-top:1rem;padding-bottom:1rem}@media(min-width:62rem){.hd{grid-template-columns:auto 1fr minmax(16rem,22rem);align-items:center;gap:2rem}}
.logo{font-weight:800;font-size:1.6rem;line-height:1;text-decoration:none;letter-spacing:-.04em}.logo b{color:#3d5bff}
nav{display:flex;gap:.4rem;overflow-x:auto;scrollbar-width:none;-webkit-overflow-scrolling:touch}nav::-webkit-scrollbar{display:none}
nav a{flex:none;text-decoration:none;font-weight:700;font-size:.82rem;color:var(--mute);padding:.4rem .8rem;border-radius:2rem;background:var(--soft)}
nav a:hover{color:var(--ink)}nav a[aria-current]{background:var(--ink);color:var(--bg)}
.sr{position:relative}.sr svg{position:absolute;left:.9rem;top:50%;transform:translateY(-50%);color:var(--mute);pointer-events:none}
.sr input{width:100%;height:2.5rem;padding:0 1rem 0 2.6rem;border:1.5px solid var(--line);background:var(--soft);color:var(--ink);font:inherit;font-size:.95rem;border-radius:3rem}.sr input:focus{border-color:var(--acc);background:var(--bg)}
#res{position:absolute;left:0;right:0;top:calc(100% + .4rem);background:var(--bg);border:1px solid var(--line);border-radius:1rem;box-shadow:0 12px 40px rgba(0,0,0,.15);z-index:5;display:none;max-height:60vh;overflow:auto}
#res a{display:block;padding:.75rem 1rem;border-bottom:1px solid var(--line);text-decoration:none;font-size:.93rem}#res a:hover{background:var(--soft)}
.intro{padding:1.75rem 0 var(--gap)}.chip{display:inline-block;background:var(--acc);color:#fff;font-size:.72rem;font-weight:800;letter-spacing:.07em;text-transform:uppercase;padding:.25rem .6rem;border-radius:.3rem}
h1{font-size:clamp(2rem,5vw,3.1rem);line-height:1.08;letter-spacing:-.035em;margin:.75rem 0 .5rem;font-weight:800}.lead{color:var(--mute);margin:0;max-width:40rem}
.g2{display:grid;gap:var(--gap) 3rem;grid-template-columns:minmax(0,1fr);margin-bottom:var(--gap)}@media(min-width:62rem){.g2{grid-template-columns:repeat(2,minmax(0,1fr))}.side{display:grid;gap:3rem;grid-template-columns:minmax(0,1fr) 19rem}}
.sh{font-size:1.2rem;margin:0;padding-bottom:.55rem;border-bottom:3px solid var(--acc);display:flex;justify-content:space-between;align-items:baseline}.sh a{font-size:.85rem;color:var(--acc);font-weight:700;text-decoration:none}.sh a:hover{text-decoration:underline}
.more{display:inline-block;margin-top:.9rem;font-size:.88rem;font-weight:700;color:var(--acc);text-decoration:none}.more:hover{text-decoration:underline}
article{padding:1rem 0;border-bottom:1px solid var(--line)}.meta{font-size:.76rem;color:var(--mute);font-weight:700}
article h3{margin:.2rem 0 .35rem;font-size:1.08rem;line-height:1.3}article h3 a{text-decoration:none}article h3 a:hover{color:var(--acc)}
article p{margin:0;font:400 .98rem/1.55 "Source Serif 4",Georgia,serif;color:var(--mute)}.empty{color:var(--mute)}
table.mk{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:.92rem;margin-bottom:var(--gap)}
.mk caption{text-align:left;font-weight:800;font-size:1.2rem;padding-bottom:.55rem;border-bottom:3px solid var(--acc)}
.mk th{font-size:.72rem;color:var(--mute);text-align:left;font-weight:700;padding:.55rem .35rem;border-bottom:1px solid var(--line)}.mk td{padding:.6rem .35rem;border-bottom:1px solid var(--line)}.mk .r{text-align:right}.mk .fv{width:2rem;padding-left:0}
.fav{background:none;border:0;color:var(--mute);font-size:1.15rem;line-height:1;cursor:pointer;padding:0}.fav[aria-pressed=true]{color:#f5a400}@media(max-width:40rem){.hide{display:none}}
.ad{margin:0 0 var(--gap);text-align:center}.ad small{display:block;font-size:.68rem;color:var(--mute);margin-bottom:.25rem;letter-spacing:.05em;text-transform:uppercase}
.slot{min-height:90px;display:grid;place-items:center;background:var(--soft);border:1px dashed var(--line);color:var(--mute);font-size:.85rem;border-radius:.4rem}
form.nl{background:var(--soft);padding:1.25rem;border-radius:.6rem;margin-bottom:var(--gap)}form.nl p{margin:.25rem 0 .9rem;font-size:.92rem}
form.nl input{width:100%;padding:.65rem .8rem;border:1px solid var(--line);background:var(--bg);color:var(--ink);font:inherit;border-radius:.4rem}
form.nl button{margin-top:.6rem;width:100%;padding:.7rem;border:0;background:var(--acc);color:#fff;font:800 1rem Manrope,sans-serif;border-radius:.4rem;cursor:pointer}
footer{margin-top:var(--gap);padding:1.75rem 0 2.5rem;border-top:1px solid var(--line);color:var(--mute);font-size:.82rem}footer a{text-decoration:underline}
.prose{max-width:42rem;font:400 1.05rem/1.7 "Source Serif 4",Georgia,serif}.prose p{margin:0 0 1.1rem}
.blk{margin-bottom:var(--gap)}.blk .mk{margin-bottom:0}.mini{font-size:.85rem}.mini th,.mini td{padding-left:.25rem;padding-right:.25rem}</style></head><body data-k="{{ kind }}">
<div class="tape" id="tape" aria-label="Prices"><div>{% for _ in range(2) %}{% for p in tape %}<span>{{ p.sym }} ${{ p.price }} <em class="{{ 'u' if p.chg>=0 else 'd' }}" style="font-style:normal">{{ '▲' if p.chg>=0 else '▼' }} {{ p.chg|abs }}%</em></span>{% endfor %}{% endfor %}</div></div>
<header><div class="w hd"><a class="logo" href="{{ root }}">Tick<b>Bit</b></a>
<nav aria-label="Sections">{% for k,t in nav %}<a href="{{ root }}{{ k }}/" {% if k==kind %}aria-current="page"{% endif %}>{{ t }}</a>{% endfor %}</nav>
<div class="sr"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg><input id="q" type="search" placeholder="Search the news" aria-label="Search the news" autocomplete="off"><div id="res"></div></div></div></header>
<main class="w">
{% if kind=='about' %}<div class="intro"><span class="chip">About</span><h1>About TickBit</h1></div>
<div class="prose"><p>TickBit is a daily briefing on AI, politics, cryptocurrencies and stocks. We collect headlines from established publications and link to the original source of every story. Excerpts come from the publishers' own feeds.</p>
<p>Political coverage draws on outlets from across the spectrum. Prices come from CoinGecko and Yahoo Finance and may be delayed.</p>
<p><strong>Everything on this site is for information only and is not financial, investment or legal advice.</strong> The site may display ads and affiliate links.</p></div>
{% elif kind=='home' %}
<div class="intro"><span class="chip">{{ today }}</span><h1>Today's News</h1><p class="lead">The day's most important stories in AI, politics, crypto and stocks.</p></div>
<div class="side"><div class="g2">{% for g in groups %}<section><h2 class="sh">{{ g.label }}<a href="{{ root }}{{ g.key }}/">See all →</a></h2>{{ feed(g.rows) }}</section>{% endfor %}</div>
<div>{{ mini(crypto[:5], 'Cryptocurrencies', 'crypto') }}{{ mini(topcap[:5], 'Stocks', 'stocks') }}{{ ad(1) }}</div></div>
{% elif kind=='favorites' %}
<div class="intro"><span class="chip">Watchlist</span><h1>Favorites</h1><p class="lead" id="favnote">Star assets on the Crypto and Stocks pages to follow them here and in the top bar.</p></div>
<div class="g2"><div>{{ table(crypto, 'Cryptocurrencies') }}</div><div>{{ table(stocks, 'Stocks') }}</div></div>
{% else %}
<div class="intro"><span class="chip">{% if mk is defined %}Markets{% else %}News{% endif %}</span><h1>{{ h1 }}</h1><p class="lead">{{ lead }}</p></div>
<div class="side"><div>{% if mk is defined %}{{ table(mk, mklabel) }}{% endif %}
<h2 class="sh">Latest news</h2>{{ feed(rows) }}</div>
<div>{{ ad(1) }}{% if newsletter %}<form class="nl" action="{{ newsletter }}" method="post"><b>The briefing in your inbox</b><p class="mute">The day's key stories. Unsubscribe at any time.</p><label class="sr-only" for="em">Email address</label><input id="em" type="email" name="email" placeholder="Email address" required autocomplete="email"><button>Subscribe</button></form>{% endif %}</div></div>
{% endif %}</main>
<footer><div class="w">© {{ year }} TickBit · For information only, not financial advice · Updated {{ updated }} · <a href="{{ root }}about/">About</a></div></footer>
<script type="application/json" id="d">{{ data|tojson }}</script>
<script>
(function(){var R="{{ root }}",F=[],P=[],D=[];
try{F=JSON.parse(localStorage.getItem("tbfav")||"[]")}catch(e){}
try{D=JSON.parse(document.getElementById("d").textContent)}catch(e){}
function tape(){try{var L=D.filter(function(x){return F.indexOf(x.s)>-1});
if(!L.length)L=D.filter(function(x){return x.k=="crypto"&&!x.z}).slice(0,3).concat(D.filter(function(x){return x.k=="stocks"}).slice(0,3));
if(!L.length)return;var t=document.querySelector("#tape div");t.textContent="";
function add(){L.forEach(function(x){var s=document.createElement("span"),e=document.createElement("em");e.className=x.c>=0?"u":"d";e.style.fontStyle="normal";
e.textContent=(x.c>=0?"▲ ":"▼ ")+Math.abs(x.c)+"%";s.append(x.s+" $"+x.p+" ",e);t.append(s)})}
var vw=document.documentElement.clientWidth,n=0;do{add();n++}while(t.scrollWidth<vw&&n<40);
var w=t.scrollWidth,k=t.childNodes.length;for(var i=0;i<k;i++)t.append(t.childNodes[i].cloneNode(true));
t.style.animationDuration=Math.max(w/55,8)+"s"}catch(e){}}
tape();window.addEventListener("load",tape);
try{var fav=document.body.dataset.k=="favorites";
document.querySelectorAll("table.mk:not(.mini) tbody").forEach(function(tb){
function paint(){var rows=Array.from(tb.querySelectorAll("tr[data-s]")),on=function(r){return F.indexOf(r.dataset.s)>-1};
rows.forEach(function(r){var b=r.querySelector(".fav");b.textContent=on(r)?"★":"☆";b.setAttribute("aria-pressed",on(r));if(fav)r.style.display=on(r)?"":"none"});
rows.sort(function(a,b){return on(b)-on(a)}).forEach(function(r){tb.appendChild(r)});
if(fav){tb.closest("table").style.display=rows.some(on)?"":"none";var n=document.getElementById("favnote");
if(n)n.textContent=F.length?"Your starred assets.":"Nothing here yet. Star assets on the Crypto and Stocks pages and they will appear here and in the top bar."}}
tb.addEventListener("click",function(e){var b=e.target.closest(".fav");if(!b)return;var s=b.closest("tr").dataset.s,i=F.indexOf(s);
i>-1?F.splice(i,1):F.push(s);try{localStorage.setItem("tbfav",JSON.stringify(F))}catch(x){}P.forEach(function(f){f()});tape()});
P.push(paint);paint()})}catch(e){}
try{var q=document.getElementById("q"),res=document.getElementById("res"),ix=null;
q.addEventListener("input",function(){var t=q.value.trim().toLowerCase();if(t.length<2){res.style.display="none";return}
(ix?Promise.resolve(ix):fetch(R+"search.json").then(function(r){return r.json()}).then(function(j){return ix=j})).then(function(d){
var h=d.filter(function(i){return(i.t+" "+i.s).toLowerCase().indexOf(t)>-1}).slice(0,10).map(function(i){var a=document.createElement("a");a.href=i.l;a.target="_blank";a.rel="noopener nofollow";a.textContent=i.t;return a});
res.replaceChildren.apply(res,h.length?h:[document.createTextNode("No results")]);res.style.display="block"})});
document.addEventListener("click",function(e){if(!e.target.closest(".sr"))res.style.display="none"})}catch(e){}})();
</script></body></html>"""

LEADS = {"ai": "The latest news in artificial intelligence.",
         "politics": "Headlines from outlets across the political spectrum.",
         "crypto": "The 15 largest cryptocurrencies by market cap, with prices, one-week trends and the latest news.",
         "stocks": "The 15 largest US stocks by market cap, with prices, 30-day trends and the latest news."}


def main():
    items = load_items()
    for i in items:
        i["when"] = ago(i["ts"])
    cr = crypto()
    tc = stocks()
    st = tc
    now = datetime.now(timezone.utc)
    base = dict(tape=[r for r in cr if r["sym"] not in STABLE][:3] + tc[:3], nav=[(k, v[0]) for k, v in TOPICS.items()] + [("favorites", "★ Favorites")],
                adsense=ADSENSE, slot=ADSENSE_SLOT, newsletter=NEWSLETTER_ACTION, year=now.year,
                updated=now.strftime("%b %d, %Y, %H:%M UTC").replace(" 0", " "), kind="", crypto=cr, stocks=st, topcap=tc,
                data=[{"s": r["sym"], "p": r["price"], "c": r["chg"], "k": r["kind"], "z": r["sym"] in STABLE} for r in cr + st])
    pages = {"": dict(kind="home", title="TickBit: daily news on AI, politics, crypto and stocks",
                      desc="The day's most important stories in AI, politics, cryptocurrencies and stocks, with market prices.",
                      today=now.strftime("%A, %B %d").replace(" 0", " "),
                      groups=[dict(key=k, label=v[1], rows=pick(items, k, 4, 2)) for k, v in TOPICS.items()])}
    for k, v in TOPICS.items():
        extra = dict(mk=cr, mklabel="Top 15 by market cap") if k == "crypto" else dict(mk=tc, mklabel="Top 15 US stocks by market cap") if k == "stocks" else {}
        pages[f"{k}/"] = dict(kind=k, title=f"{v[1]}: news and prices | TickBit" if extra else f"{v[1]}: latest news | TickBit",
                              desc=LEADS[k], h1=v[1], lead=LEADS[k], rows=pick(items, k, 30), **extra)
    pages["favorites/"] = dict(kind="favorites", title="Favorites | TickBit", desc="Your followed cryptocurrencies and stocks.")
    pages["about/"] = dict(kind="about", title="About TickBit", desc="What TickBit is and how it works.")
    tpl = Environment(autoescape=True).from_string(TPL)
    for path, ctx in pages.items():
        ctx = {**base, **ctx, "url": f"{SITE_URL}/{path}", "root": "../" * path.count("/") or "./"}
        (OUT / path).mkdir(parents=True, exist_ok=True)
        (OUT / path / "index.html").write_text(tpl.render(**ctx), "utf-8")
    (OUT / "search.json").write_text(json.dumps([{"t": i["title"], "s": i["summary"], "l": i["link"]} for i in items], ensure_ascii=False), "utf-8")
    (OUT / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                                     + "".join(f"<url><loc>{SITE_URL}/{p}</loc></url>" for p in pages) + "</urlset>", "utf-8")
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n", "utf-8")
    print(f"Done: {len(items)} stories, {len(cr)} cryptocurrencies, {len(st)} stocks.")


if __name__ == "__main__":
    main()
