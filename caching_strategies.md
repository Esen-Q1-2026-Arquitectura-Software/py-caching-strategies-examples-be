# Complete Caching Strategies Guide

**Flask (Frontend) · FastAPI (Backend) · Advanced Patterns**
*The 1% Knowledge — Expert-Level Techniques*

---

## Table of Contents

### Part 1 — Frontend Caching (HTML, CSS, JavaScript, Flask)

| # | Strategy |
|---|----------|
| [1.1](#11-http-cache-headers--the-foundation) | HTTP Cache Headers — The Foundation |
| [1.2](#12-browser-storage-apis) | Browser Storage APIs |
| [1.3](#13-service-worker-caching-pwa-patterns) | Service Worker Caching (PWA Patterns) |
| [1.4](#14-html-caching-strategies) | HTML Caching Strategies |
| [1.5](#15-css-caching-strategies) | CSS Caching Strategies |
| [1.6](#16-javascript-caching--memoization) | JavaScript Caching & Memoization |
| [1.7](#17-flask-frontend-caching) | Flask Frontend Caching |
| [1.8](#18-cdn--edge-caching-for-frontend) | CDN & Edge Caching for Frontend |
| [1.9](#19-flask-etag--conditional-request-handling) | Flask ETag & Conditional Request Handling |
| [1.10](#110-template-fragment-caching-in-flask) | Template Fragment Caching in Flask |

### Part 2 — Backend Caching (FastAPI)

| # | Strategy |
|---|----------|
| [2.1](#21-in-memory-caching-lru_cache-cachetools) | In-Memory Caching (lru_cache, cachetools) |
| [2.2](#22-redis--the-core-backend-cache) | Redis — The Core Backend Cache |
| [2.3](#23-cache-aside-lazy-loading-pattern) | Cache-Aside (Lazy Loading) Pattern |
| [2.4](#24-write-through-pattern) | Write-Through Pattern |
| [2.5](#25-write-behind-write-back-pattern) | Write-Behind (Write-Back) Pattern |
| [2.6](#26-read-through-pattern) | Read-Through Pattern |
| [2.7](#27-fastapi-response-caching-middleware) | FastAPI Response Caching Middleware |
| [2.8](#28-http-caching-with-fastapi-etag-cache-control) | HTTP Caching with FastAPI (ETag, Cache-Control) |
| [2.9](#29-async-caching-with-aiocache) | Async Caching with aiocache |
| [2.10](#210-database-query-caching-sqlalchemy) | Database Query Caching (SQLAlchemy) |
| [2.11](#211-cache-stampede-prevention-mutex-lock) | Cache Stampede Prevention (Mutex Lock) |
| [2.12](#212-thundering-herd-problem-solutions) | Thundering Herd Problem Solutions |
| [2.13](#213-two-level-cache-l1--l2) | Two-Level Cache (L1 + L2) |
| [2.14](#214-negative-caching--bloom-filters) | Negative Caching & Bloom Filters |
| [2.15](#215-cache-warming-strategies) | Cache Warming Strategies |
| [2.16](#216-event-driven-cache-invalidation) | Event-Driven Cache Invalidation |
| [2.17](#217-distributed-caching--consistent-hashing) | Distributed Caching & Consistent Hashing |
| [2.18](#218-redis-advanced-patterns) | Redis Advanced Patterns |
| [2.19](#219-fastapi-dependency-injection-for-caching) | FastAPI Dependency Injection for Caching |
| [2.20](#220-background-task-cache-refresh) | Background Task Cache Refresh |
| [2.21](#221-stale-while-revalidate-swr-pattern-in-fastapi) | Stale-While-Revalidate (SWR) Pattern in FastAPI |
| [2.22](#222-rate-limiting-with-redis-cache-adjacent) | Rate Limiting with Redis (Cache-Adjacent) |
| [2.23](#223-probabilistic-early-expiry-xfetch-algorithm) | Probabilistic Early Expiry (XFetch Algorithm) |
| [2.24](#224-cache-key-design--the-art-nobody-talks-about) | Cache Key Design — The Art Nobody Talks About |

### Reference
- [Quick Reference — Choosing a Strategy](#quick-reference--choosing-a-strategy)
- [Libraries & Tools Cheat Sheet](#libraries--tools-cheat-sheet)

---

## Part 1 — Frontend Caching

---

### 1.1 HTTP Cache Headers — The Foundation

> Directives sent in HTTP response headers that tell browsers and intermediate caches (CDNs, proxies) how long to store and when to revalidate a resource. The single most impactful caching layer — and the most misunderstood.

**Technologies:** `HTTP/1.1`, `HTTP/2`, `Nginx`, `Apache`, `Flask`, `Cloudflare`, `AWS CloudFront`

#### Key Headers

| Header | Purpose |
|--------|---------|
| `Cache-Control` | Primary cache directive (replaces `Expires` in HTTP/1.1+) |
| `ETag` | Fingerprint of the response body; enables conditional GETs |
| `Last-Modified` | Timestamp-based revalidation |
| `Vary` | Tells caches which request headers affect the response |
| `Pragma` | Legacy HTTP/1.0 — use `Cache-Control` instead |

#### `Cache-Control` Directive Cheat Sheet

| Directive | Effect |
|-----------|--------|
| `max-age=N` | Browser caches for N seconds |
| `s-maxage=N` | CDN/proxy caches for N seconds (overrides `max-age` for shared caches) |
| `no-cache` | MUST revalidate with server before using cached copy |
| `no-store` | NEVER cache (sensitive data) |
| `private` | Only browser can cache, not CDN |
| `public` | CDN can cache even if `Authorization` header present |
| `immutable` | Do NOT revalidate even on hard refresh (use with hashed filenames) |
| `stale-while-revalidate=N` | Serve stale while fetching fresh in background |
| `stale-if-error=N` | Serve stale if server is down (resilience!) |
| `must-revalidate` | Disallow stale serving even if server is down |

```python
from flask import Flask, make_response, send_file
import hashlib, json

app = Flask(__name__)

@app.route("/api/data")
def cacheable_api():
    data = {"key": "value"}
    body = json.dumps(data)
    etag = hashlib.md5(body.encode()).hexdigest()

    response = make_response(body)
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=30"
    response.headers["ETag"] = f'"{etag}"'
    response.headers["Content-Type"] = "application/json"
    return response

@app.route("/static/app.<hash>.js")
def hashed_asset(hash):
    # Immutable: hashed filename = content never changes
    response = make_response(send_file("static/app.js"))
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response
```

> **💡 1% Tip:** Use `s-maxage` separately from `max-age` to give CDNs a longer TTL than browsers.
> ```
> Cache-Control: public, max-age=60, s-maxage=3600
> ```
> Browser caches 60s, CDN caches 3600s.

> **⚠️ Warning:** `Vary: *` effectively disables CDN caching. Be specific:
> ```
> Vary: Accept-Encoding, Accept-Language   ✅
> Vary: *                                  ❌ disables CDN cache
> ```

---

### 1.2 Browser Storage APIs

> JavaScript APIs for persisting data client-side. Choosing the wrong one is a very common and costly mistake.

**Technologies:** Web APIs (built into all modern browsers), `JavaScript`

#### Storage Comparison

| API | Size | Async | Persists | Use For |
|-----|------|-------|----------|---------|
| `localStorage` | 5–10 MB | No | Forever | Simple key-value, same origin |
| `sessionStorage` | 5–10 MB | No | Tab only | Temporary session data |
| `IndexedDB` | 50–500+ MB | Yes | Forever | Large structured data |
| `Cache API` | Large | Yes | Forever | HTTP responses (Service Workers) |
| `cookies` | 4 KB | No | Configurable | Server-accessible data |

> **⚠️ Security:** Never store tokens or PII in `localStorage` — any script on the page can read it (XSS). Use `HttpOnly` cookies instead.

**localStorage with TTL** (localStorage has no native TTL — this is the fix):

```javascript
const cache = {
  set(key, value, ttlSeconds) {
    localStorage.setItem(key, JSON.stringify({
      value,
      expiresAt: Date.now() + ttlSeconds * 1000
    }));
  },
  get(key) {
    const item = localStorage.getItem(key);
    if (!item) return null;
    const { value, expiresAt } = JSON.parse(item);
    if (Date.now() > expiresAt) {
      localStorage.removeItem(key);   // lazy eviction
      return null;
    }
    return value;
  }
};

cache.set("user_profile", { name: "Alice" }, 300); // expires in 5 min
const profile = cache.get("user_profile");
```

**IndexedDB for large structured data:**

```javascript
// Use idb library (tiny wrapper) — raw IndexedDB API is painful
import { openDB } from "idb";

const db = await openDB("myAppDB", 1, {
  upgrade(db) {
    db.createObjectStore("responses", { keyPath: "url" });
  }
});

// Store response
await db.put("responses", { url: "/api/products", data: products, ts: Date.now() });

// Read with TTL check
const cached = await db.get("responses", "/api/products");
if (cached && Date.now() - cached.ts < 60_000) return cached.data;
```

> **💡 1% Tip:** IndexedDB is transactional. Use a single transaction for multiple reads/writes to avoid consistency bugs and get much better performance.

---

### 1.3 Service Worker Caching (PWA Patterns)

> A service worker is a script that runs in the background, intercepts ALL network requests from your page, and can serve from cache. The most powerful frontend caching mechanism — and the least understood.

**Technologies:** `Service Worker API`, `Cache API`, `Workbox` (Google), `PWA`

#### The 5 Core Strategies

| Strategy | When Cache Miss | Best For |
|----------|----------------|----------|
| **Cache First** | Fetch from network, store, return | Images, fonts, versioned JS/CSS |
| **Network First** | Fall back to cache | API responses that must be fresh |
| **Stale While Revalidate** | Update cache in background | News feeds, product listings |
| **Cache Only** | Fail (never goes to network) | Pre-cached offline shell |
| **Network Only** | Fail (never caches) | Analytics, payments, auth |

**Stale-While-Revalidate in a Service Worker:**

```javascript
// sw.js
self.addEventListener("fetch", event => {
  event.respondWith(staleWhileRevalidate(event.request));
});

async function staleWhileRevalidate(request) {
  const cache = await caches.open("v1");
  const cached = await cache.match(request);

  // Always fetch in background to update cache
  const networkFetch = fetch(request).then(response => {
    cache.put(request, response.clone());
    return response;
  });

  // Return cached immediately if available, else wait for network
  return cached || networkFetch;
}
```

**Workbox — production-ready declarative caching:**

```javascript
import { registerRoute } from "workbox-routing";
import { StaleWhileRevalidate, CacheFirst } from "workbox-strategies";
import { CacheableResponsePlugin } from "workbox-cacheable-response";
import { ExpirationPlugin } from "workbox-expiration";

// Static assets → Cache First, 30 day expiry
registerRoute(
  ({ request }) => request.destination === "image",
  new CacheFirst({
    cacheName: "images",
    plugins: [
      new CacheableResponsePlugin({ statuses: [200] }),
      new ExpirationPlugin({ maxEntries: 50, maxAgeSeconds: 30 * 24 * 60 * 60 })
    ]
  })
);

// API calls → Stale While Revalidate
registerRoute(
  ({ url }) => url.pathname.startsWith("/api/"),
  new StaleWhileRevalidate({ cacheName: "api-cache" })
);
```

> **💡 1% Tip:** Always version your cache names (`cache-v2`, `cache-v3`). If you deploy a new service worker but reuse the old cache name, stale assets persist indefinitely.

> **🔒 Security:** Service Workers only work on **HTTPS** (except `localhost`). This is a hard security requirement.

---

### 1.4 HTML Caching Strategies

> Techniques for caching HTML documents — often overlooked because HTML is assumed to always be dynamic.

**Technologies:** `Flask`, `Jinja2`, `Varnish`, `Nginx`, `Cloudflare`, `Eleventy`, `Hugo`

#### Strategies

**Strategy 1: Static HTML Generation**
Pre-render HTML at build time. Serves 100% from CDN. Zero server computation per request.

**Strategy 2: HTML Fragment Caching**
Cache expensive rendered sections separately, assemble on request. *(See [1.10](#110-template-fragment-caching-in-flask))*

**Strategy 3: Edge-Side Includes (ESI)**
Varnish/CDN assembles HTML from cached fragments at the edge using special ESI tags:

```html
<html>
<body>
  <esi:include src="/fragments/header" />      <!-- cached 24h -->
  <main>{{ dynamic_content }}</main>
  <esi:include src="/fragments/footer" />      <!-- cached 24h -->
</body>
</html>
```

**Strategy 4: HTML Cache-Control**
For public pages that don't change per-user, set `Cache-Control` on the HTML response:

```python
from flask import Flask, render_template, make_response

app = Flask(__name__)

@app.route("/blog/<slug>")
def blog_post(slug):
    post = get_post(slug)   # expensive DB query
    html = render_template("post.html", post=post)
    response = make_response(html)
    response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=60"
    return response
```

> **💡 1% Tip:** Use `Surrogate-Control` (Varnish-specific) to set separate CDN vs browser TTLs:
> ```
> Surrogate-Control: max-age=86400   → Varnish/CDN caches 24h
> Cache-Control: public, max-age=60  → Browser caches 60s
> ```

---

### 1.5 CSS Caching Strategies

> Techniques for caching CSS aggressively without users ever receiving stale styles after a deployment.

**Technologies:** `Webpack`, `Vite`, `PostCSS`, `Flask-Assets`, `gulp`

#### Strategy 1: Content Hashing (Fingerprinting)

Append a hash of the file's content to its filename. If content changes → filename changes → new URL → always fresh:

```
style.a3f9b2c1.css   →   Cache-Control: public, max-age=31536000, immutable
```

#### Strategy 2: Critical CSS Inlining

Extract above-the-fold CSS and inline it in `<head>`. Eliminates render-blocking. First paint is instant:

```html
<!-- templates/base.html -->
<head>
  <style>
    /* Critical CSS inlined — no network request needed */
    {{ critical_css | safe }}
  </style>
  <!-- Non-critical CSS loads async — does not block render -->
  <link rel="preload" href="/static/app.a3f9b2c1.css" as="style"
        onload="this.onload=null;this.rel='stylesheet'">
  <noscript>
    <link rel="stylesheet" href="/static/app.a3f9b2c1.css">
  </noscript>
</head>
```

#### Strategy 3: Flask-Assets Bundle + Versioning

```python
# pip install Flask-Assets webassets

from flask import Flask
from flask_assets import Environment, Bundle

app = Flask(__name__)
assets = Environment(app)
assets.versions = "hash"    # append hash to bundle name
assets.manifest = "file"    # persist version manifest

css = Bundle(
    "src/reset.css",
    "src/layout.css",
    "src/components.css",
    filters="cssmin",
    output="dist/app.%(version)s.css"
)
assets.register("css_all", css)
```

```html
{% assets "css_all" %}
  <link rel="stylesheet" href="{{ ASSET_URL }}">
{% endassets %}
```

> **💡 1% Tip:** Pre-load critical fonts to avoid FOIT (Flash of Invisible Text):
> ```html
> <link rel="preload" href="/fonts/inter.woff2" as="font" crossorigin>
> ```
> Combine with `font-display: optional` in `@font-face`.

---

### 1.6 JavaScript Caching & Memoization

> Client-side techniques for avoiding redundant computation and redundant network requests within JavaScript code.

**Technologies:** `JavaScript`, `TypeScript`, `React`, `Vue`, `SWR`, `TanStack Query`

#### Strategy 1: Memoization

Cache the result of a pure function. Same inputs → return cached output instantly:

```javascript
function memoize(fn) {
  const cache = new Map();
  return function(...args) {
    const key = JSON.stringify(args);
    if (cache.has(key)) return cache.get(key);
    const result = fn.apply(this, args);
    cache.set(key, result);
    return result;
  };
}

const expensiveCalc = memoize((n) => n * n * Math.PI);

expensiveCalc(5);   // computed
expensiveCalc(5);   // cache hit — instant
```

#### Strategy 2: Request Deduplication

If the same API call fires twice simultaneously, make only ONE request and share the result:

```javascript
const inflight = new Map();

async function fetchDeduped(url) {
  if (inflight.has(url)) return inflight.get(url);   // reuse in-flight promise

  const promise = fetch(url).then(r => r.json()).finally(() => {
    inflight.delete(url);   // clean up after completion
  });

  inflight.set(url, promise);
  return promise;
}
```

#### Strategy 3: SWR Pattern (Stale-While-Revalidate)

Return cached data immediately, re-fetch in background, update UI silently:

```javascript
const cache = new Map();

async function fetchSWR(url, onUpdate) {
  const cached = cache.get(url);
  if (cached) onUpdate(cached, false);   // instant: stale data

  const fresh = await fetch(url).then(r => r.json());
  cache.set(url, fresh);
  onUpdate(fresh, true);                  // fresh data arrived
}

fetchSWR("/api/stats", (data, isFresh) => {
  renderDashboard(data);
  if (isFresh) console.log("UI updated with fresh data");
});
```

#### Strategy 4: Debounced Cache

Prevent expensive recomputations triggered by fast user input:

```javascript
function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

const debouncedSearch = debounce(async (query) => {
  const results = await fetchSearchResults(query);
  renderResults(results);
}, 300);
```

> **💡 1% Tip:** Use `WeakMap` for memoizing functions that take objects as arguments. Unlike `Map`, `WeakMap` doesn't prevent garbage collection of keys — preventing memory leaks:
> ```javascript
> const cache = new WeakMap();
> function memoByRef(obj) {
>   if (cache.has(obj)) return cache.get(obj);
>   const result = expensiveTransform(obj);
>   cache.set(obj, result);
>   return result;
> }
> ```

---

### 1.7 Flask Frontend Caching

> Flask-specific caching tools, patterns, and configurations.

**Technologies:** `Flask`, `Flask-Caching`, `Redis`, `Memcached`, `Jinja2`

#### Supported Backends

| Backend | Description |
|---------|-------------|
| `SimpleCache` | In-memory, single process — dev/test only |
| `FileSystemCache` | Disk-based, survives restarts |
| `RedisCache` | Distributed, production-ready |
| `MemcachedCache` | Distributed, high-throughput |
| `NullCache` | Disables cache (testing) |

**Setup with Redis:**

```python
# pip install Flask-Caching redis

from flask import Flask
from flask_caching import Cache

app = Flask(__name__)
app.config["CACHE_TYPE"] = "RedisCache"
app.config["CACHE_REDIS_URL"] = "redis://localhost:6379/0"
app.config["CACHE_DEFAULT_TIMEOUT"] = 300  # 5 minutes default TTL

cache = Cache(app)
```

**`@cache.cached()` — view-level caching:**

```python
@app.route("/products")
@cache.cached(timeout=120, key_prefix="products_page")
def products():
    # This entire function only runs on cache miss
    products = db.session.query(Product).all()
    return render_template("products.html", products=products)
```

**`@cache.memoize()` — function-level caching (key includes arguments):**

```python
@cache.memoize(timeout=60)
def get_user_profile(user_id: int):
    return db.session.get(User, user_id)

get_user_profile(1)   # cache miss → stores key for user_id=1
get_user_profile(1)   # cache hit
get_user_profile(2)   # cache miss → different key
```

**Manual cache operations:**

```python
cache.set("dashboard_stats", compute_stats(), timeout=30)

stats = cache.get("dashboard_stats")
if stats is None:
    stats = compute_stats()
    cache.set("dashboard_stats", stats, timeout=30)

cache.delete("dashboard_stats")

# Delete multiple specific keys at once
cache.delete_many("key_one", "key_two", "key_three")   # explicit keys only
# NOTE: delete_many() does NOT support wildcard/glob patterns.
# For pattern-based deletion in Redis, use SCAN + DEL directly via redis-py.

cache.clear()   # nuke everything — use carefully!
```

**Per-user caching (authenticated pages):**

```python
from flask import session

@app.route("/dashboard")
@cache.cached(
    timeout=60,
    key_prefix=lambda: f"dashboard_{session.get('user_id', 'anon')}"
)
def dashboard():
    user_id = session["user_id"]
    data = get_user_dashboard_data(user_id)
    return render_template("dashboard.html", data=data)
```

**Cache with query parameters:**

```python
from flask import request

@app.route("/search")
def search():
    q = request.args.get("q", "")
    page = request.args.get("page", 1, type=int)
    cache_key = f"search_{q}_{page}"

    results = cache.get(cache_key)
    if results is None:
        results = perform_search(q, page)
        cache.set(cache_key, results, timeout=120)

    return render_template("search.html", results=results)
```

> **💡 1% Tip:** `@cache.cached()` automatically skips caching for non-GET requests and non-200 responses. Use the `unless` parameter for custom conditions:
> ```python
> @cache.cached(timeout=300, unless=lambda: current_user.is_admin)
> ```

---

### 1.8 CDN & Edge Caching for Frontend

> Content Delivery Networks cache your assets and responses at servers geographically close to users (Points of Presence / PoPs).

**Technologies:** `Cloudflare`, `AWS CloudFront`, `Fastly`, `Akamai`, `Varnish`, `Nginx`

#### Key Concepts

| Term | Meaning |
|------|---------|
| **Cache HIT** | CDN serves the response without touching your origin |
| **Cache MISS** | CDN fetches from origin, caches it, then serves |
| **TTL** | Time To Live for CDN-cached objects |
| **Purge/Invalidate** | Remove a cached object before its TTL expires |

**Flask with Cloudflare-specific headers:**

```python
@app.route("/api/public-stats")
def public_stats():
    stats = compute_stats()
    response = make_response(jsonify(stats))

    response.headers["Cache-Control"] = "public, max-age=60"
    # Cloudflare Cache Tags — for granular purging (Enterprise plan)
    response.headers["Cache-Tag"] = "stats,dashboard,global"

    return response
```

**Cache purging via Cloudflare API:**

```python
import requests

def purge_cloudflare_cache(tags: list[str]):
    """Purge Cloudflare cache by tag — instant CDN invalidation"""
    r = requests.post(
        f"https://api.cloudflare.com/client/v4/zones/{ZONE_ID}/purge_cache",
        headers={"Authorization": f"Bearer {CF_TOKEN}"},
        json={"tags": tags}
    )
    r.raise_for_status()

def save_post(post):
    db.save(post)
    purge_cloudflare_cache(["posts", f"post-{post.id}"])   # targeted purge
```

> **💡 1% Tip:** Cloudflare supports `stale-while-revalidate` — serve stale at the CDN edge while origin refreshes. Zero user-visible latency during cache refresh.

> **📖 AWS CloudFront note:** CloudFront honors both `s-maxage` and `max-age`. `s-maxage` takes precedence when both are present. If neither is set, CloudFront falls back to a **24-hour default TTL**. Always set `s-maxage` explicitly for CDN control and `max-age` for browser control independently.

---

### 1.9 Flask ETag & Conditional Request Handling

> ETags allow browsers to ask "has this changed?" instead of downloading the full response. Server responds with `304 Not Modified` (no body) if unchanged — saving bandwidth and reducing latency.

**Technologies:** `Flask`, `HTTP/1.1` conditional requests

**ETag implementation:**

```python
import hashlib
from flask import request, make_response, jsonify

def generate_etag(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()[:16]

@app.route("/api/config")
def get_config():
    config = fetch_config_from_db()
    body = str(config)
    etag = f'"{generate_etag(body)}"'

    if request.headers.get("If-None-Match") == etag:
        return "", 304   # Not Modified — no body sent, bandwidth saved!

    response = make_response(jsonify(config))
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, no-cache"
    return response
```

**Last-Modified conditional caching:**

```python
from datetime import timezone
from email.utils import parsedate_to_datetime, format_datetime

@app.route("/api/post/<int:post_id>")
def get_post(post_id):
    post = Post.query.get_or_404(post_id)
    last_modified = post.updated_at.replace(tzinfo=timezone.utc)
    last_modified_str = format_datetime(last_modified)

    ims = request.headers.get("If-Modified-Since")
    if ims:
        client_time = parsedate_to_datetime(ims)
        if last_modified <= client_time:
            return "", 304   # Not Modified

    response = make_response(jsonify(post.to_dict()))
    response.headers["Last-Modified"] = last_modified_str
    response.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
    return response
```

> **💡 1% Tip:** ETag should change when ANY part of the response changes, including `Content-Type` and headers that affect rendering. Hash the full response body, not just the data model version.

---

### 1.10 Template Fragment Caching in Flask

> Cache expensive sub-sections of a Jinja2 template independently. The holy grail of template rendering performance.

**Technologies:** `Flask`, `Jinja2`, `Flask-Caching`, `Redis`

**Fragment caching with Flask-Caching:**

```python
@cache.memoize(timeout=3600)
def render_navigation():
    categories = Category.query.all()
    return render_template("fragments/nav.html", categories=categories)

@cache.memoize(timeout=300)
def render_featured_products():
    products = Product.query.filter_by(featured=True).limit(8).all()
    return render_template("fragments/featured.html", products=products)

@app.route("/")
def homepage():
    hero_data = get_hero_data()                # fresh every request
    nav_html = render_navigation()             # cached 1h
    featured_html = render_featured_products() # cached 5m

    return render_template("home.html",
        hero=hero_data,
        nav_fragment=nav_html,
        featured_fragment=featured_html
    )
```

```html
<!-- templates/home.html -->
{{ nav_fragment | safe }}
<section class="hero">{{ hero.title }}</section>
{{ featured_fragment | safe }}
```

**Jinja2 custom `{% cache %}` tag extension (advanced):**

```python
from jinja2 import nodes
from jinja2.ext import Extension

class CacheExtension(Extension):
    tags = {"cache"}

    def parse(self, parser):
        lineno = next(parser.stream).lineno
        timeout = parser.parse_expression()
        key = parser.parse_expression()
        body = parser.parse_statements(["name:endcache"], drop_needle=True)
        return nodes.CallBlock(
            self.call_method("_cache_support", [timeout, key]),
            [], [], body
        ).set_lineno(lineno)

    def _cache_support(self, timeout, key, caller):
        rv = cache.get(key)
        if rv is None:
            rv = caller()
            cache.set(key, rv, timeout=timeout)
        return rv

app.jinja_env.add_extension(CacheExtension)
```

```html
{% cache 300, "sidebar_" ~ user.id %}
  <aside>{{ render_sidebar() }}</aside>
{% endcache %}
```

> **🔒 Security:** Never cache fragments that contain CSRF tokens, session data, or user-specific security indicators. Fragment caching can inadvertently leak one user's security token to another.

---

## Part 2 — Backend Caching (FastAPI)

---

### 2.1 In-Memory Caching (lru_cache, cachetools)

> Cache results directly in Python process memory. Fastest possible cache (nanosecond access) but lost on restart and not shared across instances.

**Technologies:** `Python stdlib (functools)`, `cachetools`, `FastAPI`

**`functools.lru_cache`:**

```python
from functools import lru_cache
from fastapi import FastAPI

app = FastAPI()

@lru_cache(maxsize=256)
def get_country_config(country_code: str) -> dict:
    """Cached in memory for lifetime of process. maxsize=256 → LRU eviction"""
    return db.fetch_country_config(country_code)

@app.get("/config/{country}")
def config(country: str):
    return get_country_config(country.upper())
```

**TTL cache with `cachetools`:**

```python
# pip install cachetools

from cachetools import TTLCache, cached
from cachetools.keys import hashkey
import threading

# Thread-safe TTL cache: max 500 items, each lives 60 seconds
_cache = TTLCache(maxsize=500, ttl=60)
_lock = threading.Lock()

@cached(cache=_cache, key=lambda user_id: hashkey(user_id), lock=_lock)
def get_user_permissions(user_id: int) -> list[str]:
    return db.fetch_permissions(user_id)
```

**Async-aware in-memory cache:**

```python
from functools import wraps
from cachetools import TTLCache

_async_cache: TTLCache = TTLCache(maxsize=100, ttl=30)

def async_cached(cache: TTLCache):
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            key = (fn.__name__, args, tuple(sorted(kwargs.items())))
            if key in cache:
                return cache[key]
            result = await fn(*args, **kwargs)
            cache[key] = result
            return result
        return wrapper
    return decorator

@async_cached(_async_cache)
async def fetch_rate_from_api(currency: str) -> float:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"https://api.rates.io/{currency}")
        return r.json()["rate"]
```

> **💡 1% Tip:** `lru_cache` on class methods is dangerous — `self` is part of the cache key and holds a reference to the object, preventing garbage collection. Apply `@lru_cache` only to module-level functions.

---

### 2.2 Redis — The Core Backend Cache

> Redis (Remote Dictionary Server) is an in-memory data structure store used as cache, message broker, and database. The industry standard for distributed caching.

**Technologies:** `Redis`, `redis-py`, `aioredis` (merged into redis-py), `Valkey`

#### Relevant Data Structures

| Structure | Cache Use Case |
|-----------|---------------|
| `STRING` | Simple key-value (most common) |
| `HASH` | Cache an object's fields separately |
| `LIST` | Ordered cache, queues |
| `SET` | Unique member cache ("which IDs are cached?") |
| `SORTED SET` | Leaderboards, time-windowed rate limiting |
| `STREAM` | Event log for cache invalidation messages |

**Async Redis with FastAPI:**

```python
# pip install redis[asyncio]

import redis.asyncio as aioredis
from fastapi import FastAPI, Depends
import json

app = FastAPI()

async def get_redis():
    r = aioredis.from_url(
        "redis://localhost:6379",
        encoding="utf-8",
        decode_responses=True,
        max_connections=20       # connection pool
    )
    try:
        yield r
    finally:
        await r.aclose()

@app.get("/products/{product_id}")
async def get_product(product_id: int, redis = Depends(get_redis)):
    cache_key = f"product:{product_id}"
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)

    product = await db.fetch_product(product_id)
    await redis.setex(cache_key, 300, json.dumps(product))  # TTL = 300s
    return product
```

**Redis connection pool via lifespan:**

```python
from contextlib import asynccontextmanager
import redis.asyncio as aioredis

redis_pool: aioredis.Redis = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_pool
    redis_pool = aioredis.from_url("redis://localhost:6379", decode_responses=True)
    yield
    await redis_pool.aclose()

app = FastAPI(lifespan=lifespan)
```

> **💡 1% Tip:** Use `OBJECT ENCODING key` in the Redis CLI to inspect internal encoding. Strings under 44 bytes use `embstr` — 3× more memory-efficient than `raw`. Keep cache keys short.

> **💡 1% Tip:** `OBJECT FREQ` and `OBJECT IDLETIME` let you identify the hottest and coldest keys — invaluable for sizing your cache and eviction policy tuning.

---

### 2.3 Cache-Aside (Lazy Loading) Pattern

> The most widely used caching pattern. Application checks cache first. On miss, fetches from DB, stores in cache, returns. Cache is populated lazily — only requested items get cached.

```
Request → Cache HIT? → Return cached value
              ↓ MISS
         Fetch from DB → Store in Cache → Return value
```

| | |
|---|---|
| **Pros** | Only cache what's actually needed; cache failure doesn't break the app |
| **Cons** | First request always slow (cold start); stale data risk |

```python
import json
import redis.asyncio as aioredis

async def get_user(user_id: int, redis: aioredis.Redis) -> dict:
    cache_key = f"user:{user_id}"

    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)

    user = await db.fetch_user(user_id)
    if user is None:
        await redis.setex(cache_key, 30, "null")   # negative cache
        return None

    await redis.setex(cache_key, 600, json.dumps(user))
    return user
```

**Generic cache-aside decorator:**

```python
from functools import wraps
import json

def cache_aside(key_fn, ttl: int = 300):
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, redis=None, **kwargs):
            key = key_fn(*args, **kwargs)
            cached = await redis.get(key)
            if cached is not None:
                return json.loads(cached)
            result = await fn(*args, **kwargs)
            await redis.setex(key, ttl, json.dumps(result))
            return result
        return wrapper
    return decorator

@cache_aside(key_fn=lambda uid: f"profile:{uid}", ttl=600)
async def fetch_profile(uid: int):
    return await db.get_profile(uid)
```

---

### 2.4 Write-Through Pattern

> Every write to the database ALSO writes to cache synchronously. Cache is always consistent with the DB.

```
Write Request → Write to DB + Write to Cache (atomic) → Respond
```

| | |
|---|---|
| **Pros** | Cache always fresh after writes; no stale reads after updates |
| **Cons** | Write latency increases (two writes); cache polluted with unread data |

```python
@app.put("/users/{user_id}")
async def update_user(user_id: int, data: UserUpdate, redis=Depends(get_redis)):
    # 1. Write to database
    updated_user = await db.update_user(user_id, data.dict())

    # 2. IMMEDIATELY update cache (write-through)
    cache_key = f"user:{user_id}"
    await redis.setex(cache_key, 600, json.dumps(updated_user))

    return updated_user
```

> **💡 1% Tip:** Always combine write-through with a TTL as a safety net. If a cache update is ever missed due to a race condition, the TTL guarantees eventual consistency. TTL is your last line of defense.

---

### 2.5 Write-Behind (Write-Back) Pattern

> Write to cache first, acknowledge to client immediately, then write to DB asynchronously in the background. Highest write performance, highest risk.

```
Write → Cache (instant ack) → Background Worker → DB (async)
```

| | |
|---|---|
| **Pros** | Extremely fast writes; batching reduces DB load |
| **Cons** | Data loss risk if cache crashes before DB write; added complexity |

```python
@app.post("/events")
async def record_event(event: EventIn, redis=Depends(get_redis)):
    event_data = event.dict()

    # 1. Write to cache immediately (instant response to user)
    await redis.setex(f"event:{event.id}", 3600, json.dumps(event_data))

    # 2. Queue for async DB persistence via Redis Stream
    await redis.xadd("db:write:events", event_data)   # non-blocking

    return {"status": "queued", "id": event.id}

# Separate worker process
async def db_writer_worker(redis):
    while True:
        messages = await redis.xread({"db:write:events": ">"}, count=100, block=1000)
        if messages:
            events = [m[1] for _, m in messages[0][1]]
            await db.bulk_insert_events(events)   # batched insert
```

---

### 2.6 Read-Through Pattern

> The cache itself is responsible for fetching from the database on a miss. The application ONLY talks to the cache — never directly to the DB.

```
App → Cache → HIT: return
            → MISS: Cache fetches from DB, stores, returns
```

| | |
|---|---|
| **Pros** | Application code is clean — no manual cache logic |
| **Cons** | Requires a cache client that supports this pattern (`aiocache` does) |

```python
# pip install aiocache

from aiocache import Cache
from aiocache.decorators import cached

cache = Cache(Cache.REDIS, endpoint="localhost", port=6379, namespace="main")

@cached(ttl=300, cache=Cache.REDIS, namespace="users")
async def get_user(user_id: int) -> dict:
    """aiocache makes this read-through automatically"""
    # This only runs on cache miss — cache handles the rest
    return await db.fetch_user(user_id)

@app.get("/user/{user_id}")
async def user_endpoint(user_id: int):
    return await get_user(user_id)
```

---

### 2.7 FastAPI Response Caching Middleware

> A middleware layer that caches full HTTP responses at the FastAPI level. All routes benefit with zero per-route cache code.

**Technologies:** `FastAPI`, `Starlette Middleware`, `Redis`

```python
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import redis.asyncio as aioredis
import json

class ResponseCacheMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, redis_url: str, ttl: int = 60):
        super().__init__(app)
        self.redis = aioredis.from_url(redis_url)
        self.ttl = ttl

    async def dispatch(self, request: Request, call_next):
        if request.method != "GET":
            return await call_next(request)

        if request.headers.get("X-No-Cache"):
            return await call_next(request)

        cache_key = f"response:{request.url.path}?{request.url.query}"
        cached = await self.redis.get(cache_key)

        if cached:
            data = json.loads(cached)
            return Response(
                content=data["body"],
                status_code=data["status_code"],
                headers={**data["headers"], "X-Cache": "HIT"}
            )

        response = await call_next(request)

        if response.status_code == 200:
            body = b"".join([chunk async for chunk in response.body_iterator])
            await self.redis.setex(cache_key, self.ttl, json.dumps({
                "body": body.decode(),
                "status_code": response.status_code,
                "headers": dict(response.headers)
            }))
            return Response(
                content=body,
                status_code=response.status_code,
                headers={**dict(response.headers), "X-Cache": "MISS"}
            )

        return response

app.add_middleware(ResponseCacheMiddleware, redis_url="redis://localhost:6379")
```

---

### 2.8 HTTP Caching with FastAPI (ETag, Cache-Control)

> Setting proper HTTP caching headers in FastAPI responses to enable browser and CDN caching.

**Technologies:** `FastAPI`, `Starlette Response`

**ETag implementation:**

```python
import hashlib, json
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

@app.get("/api/catalog")
async def get_catalog(request: Request):
    catalog = await fetch_catalog_from_db()
    body = json.dumps(catalog, sort_keys=True)
    etag = f'"{hashlib.md5(body.encode()).hexdigest()}"'

    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)

    return JSONResponse(
        content=catalog,
        headers={
            "ETag": etag,
            "Cache-Control": "public, max-age=60, stale-while-revalidate=30",
        }
    )
```

**Cache-Control by endpoint type:**

```python
# Immutable public resource (versioned)
@app.get("/api/v1/constants")
async def constants():
    return JSONResponse(
        content=CONSTANTS,
        headers={"Cache-Control": "public, max-age=86400, immutable"}
    )

# User-specific private (never CDN cached)
@app.get("/api/me")
async def me(current_user=Depends(get_current_user)):
    return JSONResponse(
        content=current_user.dict(),
        headers={"Cache-Control": "private, max-age=60"}
    )

# Real-time data — validate always, serve stale ok
@app.get("/api/stock/{ticker}")
async def stock_price(ticker: str):
    price = await get_stock_price(ticker)
    return JSONResponse(
        content={"ticker": ticker, "price": price},
        headers={"Cache-Control": "public, no-cache, stale-while-revalidate=10"}
    )
```

---

### 2.9 Async Caching with aiocache

> `aiocache` is an async-native caching library that integrates seamlessly with FastAPI. Supports Redis, Memcached, and in-memory backends with a unified API.

**Technologies:** `aiocache`, `Redis`, `Memcached`, `MsgPack`

```python
# pip install aiocache[redis] msgpack

from aiocache import caches, Cache
from aiocache.serializers import MsgPackSerializer

caches.set_config({
    "default": {
        "cache": "aiocache.RedisCache",
        "endpoint": "localhost",
        "port": 6379,
        "ttl": 300,
        "serializer": {"class": "aiocache.serializers.MsgPackSerializer"},
        "plugins": [
            {"class": "aiocache.plugins.HitMissRatioPlugin"},  # metrics
            {"class": "aiocache.plugins.TimingPlugin"},        # timing
        ]
    }
})
```

**Multi-key batch fetching:**

```python
from aiocache.decorators import cached, multi_cached

@cached(ttl=60, key_builder=lambda fn, *args, **kw: f"order:{args[0]}")
async def get_order(order_id: int) -> dict:
    return await db.fetch_order(order_id)

@multi_cached(keys_from_attr="ids", ttl=120)
async def get_products(ids: list[int]) -> dict:
    # Called ONLY for IDs not in cache — batch DB fetch for misses
    results = await db.fetch_products_by_ids(ids)
    return {p["id"]: p for p in results}
```

> **💡 1% Tip:** MsgPack serialization is **2–3× faster** and **30–50% smaller** than JSON for Redis payloads. Use it for high-throughput services: `pip install aiocache[redis] msgpack`

---

### 2.10 Database Query Caching (SQLAlchemy)

> Caching results of expensive database queries to avoid repeated heavy SQL operations.

**Technologies:** `SQLAlchemy`, `FastAPI`, `Redis`, `dogpile.cache`

**Query result caching with Redis:**

```python
import json, hashlib
from sqlalchemy import text

async def cached_query(query: str, params: dict, ttl: int = 300):
    key = "sql:" + hashlib.md5(f"{query}{params}".encode()).hexdigest()

    cached = await redis.get(key)
    if cached:
        return json.loads(cached)

    async with db.begin() as conn:
        result = await conn.execute(text(query), params)
        rows = [dict(r._mapping) for r in result.fetchall()]

    await redis.setex(key, ttl, json.dumps(rows, default=str))
    return rows

# Usage
products = await cached_query(
    "SELECT * FROM products WHERE category_id = :cid ORDER BY name",
    {"cid": 5},
    ttl=120
)
```

**SQLAlchemy + `dogpile.cache` (ORM-level):**

```python
# pip install dogpile.cache

from dogpile.cache import make_region

region = make_region().configure(
    "dogpile.cache.redis",
    expiration_time=300,
    arguments={"host": "localhost", "port": 6379, "db": 0}
)

class CachingQuery(Query):
    def get_cache_key(self):
        return str(self.statement.compile(compile_kwargs={"literal_binds": True}))

    def cached(self, ttl=300):
        key = self.get_cache_key()
        result = region.get(key)
        if result is NO_VALUE:
            result = self.all()
            region.set(key, result, expiration_time=ttl)
        return result
```

> **💡 1% Tip:** Run `EXPLAIN ANALYZE` on your most-cached queries before caching them. Sometimes a missing DB index is faster to add than introducing cache consistency complexity — and an index is always fresh.

---

### 2.11 Cache Stampede Prevention (Mutex Lock)

> When a popular cache key expires, multiple simultaneous requests all find a cache miss and all query the database at once — overwhelming it. This is the **cache stampede** (a.k.a. dogpile effect). Solution: only ONE request rebuilds the cache; others wait.

**Technologies:** `Redis`, `asyncio`, `Redlock algorithm`

```python
import asyncio, json, time

async def get_with_lock(redis, key: str, fetch_fn, ttl: int = 300):
    cached = await redis.get(key)
    if cached and cached != "BUILDING":
        return json.loads(cached)

    lock_key = f"lock:{key}"
    # NX = only set if not exists — atomic lock acquisition
    acquired = await redis.set(lock_key, "1", nx=True, ex=10)

    if acquired:
        try:
            result = await fetch_fn()
            await redis.setex(key, ttl, json.dumps(result))
            return result
        finally:
            await redis.delete(lock_key)
    else:
        # Another process is rebuilding — wait and retry
        for _ in range(20):   # wait up to 2 seconds
            await asyncio.sleep(0.1)
            cached = await redis.get(key)
            if cached and cached != "BUILDING":
                return json.loads(cached)
        return await fetch_fn()   # fallback if wait timed out

@app.get("/trending")
async def trending(redis=Depends(get_redis)):
    return await get_with_lock(
        redis,
        "trending:global",
        lambda: db.compute_trending_items(),
        ttl=60
    )
```

> **💡 1% Tip:** Instead of the `"BUILDING"` sentinel, store the **old stale value** temporarily while rebuilding. Users get slightly stale data rather than waiting — much better UX under stampede conditions.

---

### 2.12 Thundering Herd Problem Solutions

> Similar to stampede but at system startup — all caches are cold simultaneously. Solutions focus on preventing mass cache miss bursts.

#### Strategy 1: TTL Jitter (Randomized Expiry)

Add random offset to TTL so keys don't all expire at the same timestamp:

```python
import random

def ttl_with_jitter(base_ttl: int, jitter_pct: float = 0.1) -> int:
    """Add ±10% random jitter to base TTL"""
    jitter = int(base_ttl * jitter_pct)
    return base_ttl + random.randint(-jitter, jitter)

await redis.setex(key, ttl_with_jitter(300), value)  # 270–330s instead of exactly 300
```

#### Strategy 2: Circuit Breaker

If the DB is failing, stop hammering it — return cached/default values instead:

```python
class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=30):
        self.failures = 0
        self.threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.opened_at = None
        self.state = "closed"   # closed=normal, open=blocking, half-open=testing

    async def call(self, fn, *args, fallback=None, **kwargs):
        if self.state == "open":
            if time.time() - self.opened_at > self.recovery_timeout:
                self.state = "half-open"
            else:
                return fallback   # circuit open — return fallback immediately

        try:
            result = await fn(*args, **kwargs)
            self.failures = 0
            self.state = "closed"
            return result
        except Exception:
            self.failures += 1
            if self.failures >= self.threshold:
                self.state = "open"
                self.opened_at = time.time()
            raise

db_breaker = CircuitBreaker()

@app.get("/products")
async def products():
    cached = await redis.get("products:all")
    if cached:
        return json.loads(cached)

    result = await db_breaker.call(
        db.fetch_all_products,
        fallback=[]   # return empty list if circuit open
    )
    await redis.setex("products:all", 300, json.dumps(result))
    return result
```

---

### 2.13 Two-Level Cache (L1 + L2)

> L1 = fast local in-process memory (nanosecond access). L2 = shared Redis (~1ms). Dramatically reduces Redis round-trips for frequently accessed hot data.

```
L1 (local TTLCache)  →  L2 (Redis)  →  L3 (Database)
  nanoseconds            ~1ms             ~10ms+
```

```python
from cachetools import TTLCache

# L1: local in-memory, very short TTL to limit staleness
_l1_cache: TTLCache = TTLCache(maxsize=200, ttl=5)   # 5-second TTL

async def get_with_l1_l2(key: str, fetch_fn, l1_ttl=5, l2_ttl=300):
    # L1 check — no network
    if key in _l1_cache:
        return _l1_cache[key]

    # L2 check — Redis
    cached = await redis.get(key)
    if cached:
        result = json.loads(cached)
        _l1_cache[key] = result   # backfill L1
        return result

    # DB fetch — populate both caches
    result = await fetch_fn()
    await redis.setex(key, l2_ttl, json.dumps(result))
    _l1_cache[key] = result
    return result

@app.get("/config")
async def get_config():
    return await get_with_l1_l2("app:config", db.load_config, l1_ttl=5, l2_ttl=600)
```

> **💡 1% Tip:** L1 is process-local. In a multi-instance deployment each instance has its own L1 — maximum staleness equals your L1 TTL (5 seconds above). For real-time data (prices, availability), set L1 TTL to `0` or skip L1 entirely.

---

### 2.14 Negative Caching & Bloom Filters

> Cache "this thing doesn't exist" to prevent repeated DB lookups for missing data. Classic attack: bombard API with non-existent IDs → each request hits DB. Negative caching + Bloom filters shut this down.

**Technologies:** `Redis`, `RedisBloom` module (`BF.ADD` / `BF.EXISTS`)

**Strategy 1: Negative Cache (simple)**

```python
CACHE_MISS_SENTINEL = "__NOT_FOUND__"

async def get_user_safe(user_id: int) -> dict | None:
    cached = await redis.get(f"user:{user_id}")

    if cached == CACHE_MISS_SENTINEL:
        return None   # known not found — no DB hit

    if cached:
        return json.loads(cached)

    user = await db.fetch_user(user_id)

    if user is None:
        await redis.setex(f"user:{user_id}", 30, CACHE_MISS_SENTINEL)  # short TTL
        return None

    await redis.setex(f"user:{user_id}", 600, json.dumps(user))
    return user
```

**Strategy 2: Bloom Filter** — probabilistic, zero false negatives, ~1.2 MB for 1 million IDs:

```python
# Requires RedisBloom module (redis-stack includes it)

async def might_exist(redis, item_id: int) -> bool:
    """False = definitely not in DB. True = might exist."""
    return await redis.execute_command("BF.EXISTS", "users:bloom", str(item_id))

async def add_to_bloom(redis, item_id: int):
    await redis.execute_command("BF.ADD", "users:bloom", str(item_id))

async def create_user(data: dict):
    user = await db.create_user(data)
    await add_to_bloom(redis, user["id"])
    return user

async def get_user(user_id: int) -> dict | None:
    if not await might_exist(redis, user_id):
        return None   # definitively not in DB — no query needed
    return await db.fetch_user(user_id)
```

---

### 2.15 Cache Warming Strategies

> Pre-populate cache before traffic arrives to avoid cold-start latency spikes. Essential for deployments, cache flushes, and planned high-traffic events.

**Technologies:** `FastAPI lifespan`, `APScheduler`, `Redis PIPELINE`

**Strategy 1: Startup Warming**

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Warming caches...")
    await warm_product_catalog()
    await warm_category_tree()
    await warm_site_config()
    print("Cache warm — ready to serve")
    yield

async def warm_product_catalog():
    products = await db.fetch_all_products()
    pipe = redis.pipeline()
    for product in products:
        pipe.setex(f"product:{product['id']}", 3600, json.dumps(product))
    await pipe.execute()   # single round-trip for ALL writes
```

**Strategy 2: Scheduled Proactive Refresh (before TTL expires)**

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

@scheduler.scheduled_job("interval", seconds=240)  # refresh every 4m (TTL=5m)
async def refresh_trending():
    """Refresh before 5m TTL expires — users never see a cache miss"""
    data = await db.compute_trending()
    await redis.setex("trending:global", 300, json.dumps(data))
```

> **💡 1% Tip:** Redis **PIPELINE** batches all commands into one TCP round-trip, making bulk cache warming **10–100× faster** than individual SET commands.

---

### 2.16 Event-Driven Cache Invalidation

> Push cache invalidation events when data changes. Cache stays fresh without relying on TTL expiry alone.
>
> *Phil Karlton: "There are only two hard things in CS: cache invalidation and naming things."*

**Technologies:** `Redis Pub/Sub`, `Redis Streams`, `FastAPI`, `Celery`

**Cache invalidation via Redis Pub/Sub:**

```python
async def update_product(product_id: int, data: dict):
    updated = await db.update_product(product_id, data)

    await redis.delete(f"product:{product_id}")   # invalidate directly

    # Notify OTHER service instances to invalidate THEIR L1 caches
    await redis.publish(
        "cache:invalidate",
        json.dumps({"type": "product", "id": product_id})
    )
    return updated

async def cache_invalidation_listener():
    pubsub = redis.pubsub()
    await pubsub.subscribe("cache:invalidate")

    async for message in pubsub.listen():
        if message["type"] == "message":
            event = json.loads(message["data"])
            if event["type"] == "product":
                _l1_cache.pop(f"product:{event['id']}", None)
```

**Tag-based cache invalidation:**

```python
async def cache_set_with_tags(key: str, value, ttl: int, tags: list[str]):
    pipe = redis.pipeline()
    pipe.setex(key, ttl, json.dumps(value))
    for tag in tags:
        pipe.sadd(f"tag:{tag}", key)
        pipe.expire(f"tag:{tag}", ttl)
    await pipe.execute()

async def invalidate_by_tag(tag: str):
    """Delete ALL cache keys associated with a tag in one operation"""
    keys = await redis.smembers(f"tag:{tag}")
    if keys:
        await redis.delete(*keys, f"tag:{tag}")

# Cache a product under two tags
await cache_set_with_tags(
    f"product:{product.id}",
    product.dict(),
    ttl=600,
    tags=["products", f"category:{product.category_id}"]
)

# When category changes: invalidate all its products instantly
await invalidate_by_tag(f"category:{category_id}")
```

---

### 2.17 Distributed Caching & Consistent Hashing

> When you have multiple Redis nodes (Redis Cluster), keys must be distributed evenly. Consistent hashing ensures adding/removing nodes only re-maps a small fraction of keys.

**Technologies:** `Redis Cluster`, `Redis Sentinel`, `Twemproxy`, `Envoy`

```python
from redis.asyncio.cluster import RedisCluster

# Async mode is implicit when importing from redis.asyncio.cluster
# There is NO async_mode parameter — async client is selected by import path
redis_cluster = RedisCluster(
    host="redis-cluster-node1",
    port=6379,
    skip_full_coverage_check=True,
)

await redis_cluster.set("user:123", json.dumps(user_data))
```

**Hash tags for co-locating related keys on the same node:**

```python
async def get_user_bundle(user_id: int):
    """Fetch user profile + permissions + settings atomically"""
    pipe = redis_cluster.pipeline()

    # {} hash tags guarantee all 3 keys land on the same cluster node
    pipe.get(f"{{user:{user_id}}}:profile")
    pipe.get(f"{{user:{user_id}}}:perms")
    pipe.get(f"{{user:{user_id}}}:settings")

    profile, perms, settings = await pipe.execute()
    return {
        "profile": json.loads(profile) if profile else None,
        "permissions": json.loads(perms) if perms else [],
        "settings": json.loads(settings) if settings else {}
    }
```

> **💡 1% Tip:** Redis Cluster divides the keyspace into **16384 slots**: `slot = CRC16(key) % 16384`. Without hash tags, multi-key operations across different slots will throw `CROSSSLOT` errors. Always use `{bracket}` tags for keys that must be co-located.

---

### 2.18 Redis Advanced Patterns

> Advanced Redis operations that unlock performance and functionality beyond simple GET/SET.

#### Pattern 1: Pipeline (Batch Commands)

```python
async def bulk_cache_lookup(ids: list[int]) -> dict:
    pipe = redis.pipeline()
    for id in ids:
        pipe.get(f"item:{id}")
    results = await pipe.execute()   # ONE round-trip for all GETs
    return {ids[i]: json.loads(v) for i, v in enumerate(results) if v}
```

#### Pattern 2: Lua Scripts (Atomic Operations)

```python
GET_OR_SET_LUA = """
local val = redis.call('GET', KEYS[1])
if val then
    return {1, val}
end
redis.call('SET', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[2])
return {0, ARGV[1]}
"""

async def atomic_get_or_set(key: str, value: str, ttl: int):
    """Atomically: return existing value OR set new one. No race conditions."""
    result = await redis.eval(GET_OR_SET_LUA, 1, key, value, str(ttl))
    was_hit = result[0] == 1
    return was_hit, result[1]
```

#### Pattern 3: Hashes for Structured Objects

```python
# Update a single field without re-serializing the whole object
async def update_user_field(user_id: int, field: str, value):
    await redis.hset(f"user:{user_id}", field, json.dumps(value))

async def get_user_field(user_id: int, field: str):
    val = await redis.hget(f"user:{user_id}", field)
    return json.loads(val) if val else None
```

#### Pattern 4: Sorted Sets for Ranked Data

```python
async def update_leaderboard(user_id: int, score: float):
    await redis.zadd("leaderboard:global", {str(user_id): score})
    await redis.expire("leaderboard:global", 3600)

async def get_top_n(n: int = 10) -> list:
    return await redis.zrevrange("leaderboard:global", 0, n - 1, withscores=True)
```

> **💡 1% Tip:** Redis stores integers set via `redis.set("count", 42)` using its internal `int` encoding — only 8 bytes vs 20+ bytes for a raw string. Always store integers as integers, not string-encoded numbers.

---

### 2.19 FastAPI Dependency Injection for Caching

> Use FastAPI's DI system to inject a clean cache service into any endpoint — keeps cache logic out of route handlers and makes unit testing trivial.

**Technologies:** `FastAPI`, `Depends()`, `Redis`

```python
from fastapi import FastAPI, Depends
from typing import Any
import json

class CacheService:
    def __init__(self, redis: aioredis.Redis, default_ttl: int = 300):
        self.redis = redis
        self.default_ttl = default_ttl

    async def get(self, key: str) -> Any | None:
        val = await self.redis.get(key)
        return json.loads(val) if val else None

    async def set(self, key: str, value: Any, ttl: int | None = None):
        await self.redis.setex(key, ttl or self.default_ttl, json.dumps(value))

    async def invalidate(self, *keys: str):
        if keys:
            await self.redis.delete(*keys)

    async def get_or_set(self, key: str, fetch_fn, ttl: int | None = None) -> Any:
        cached = await self.get(key)
        if cached is not None:
            return cached
        result = await fetch_fn()
        await self.set(key, result, ttl)
        return result

def get_cache_service(redis=Depends(get_redis)) -> CacheService:
    return CacheService(redis)

@app.get("/orders/{order_id}")
async def get_order(order_id: int, cache: CacheService = Depends(get_cache_service)):
    return await cache.get_or_set(
        f"order:{order_id}",
        lambda: db.fetch_order(order_id),
        ttl=120
    )
```

> **💡 1% Tip:** In unit tests, swap the real service using `dependency_overrides` — no Redis needed:
> ```python
> app.dependency_overrides[get_cache_service] = lambda: FakeCacheService()
> ```

---

### 2.20 Background Task Cache Refresh

> When a cache item is stale, trigger a background refresh AFTER serving the stale response. Users get an instant response; the cache updates silently. This is the server-side implementation of Stale-While-Revalidate.

**Technologies:** `FastAPI BackgroundTasks`, `asyncio.create_task`

```python
from fastapi import BackgroundTasks
import time

_local_cache: dict = {}   # key → (value, expires_at, is_refreshing)

async def background_refresh(key: str, fetch_fn, ttl: int):
    fresh = await fetch_fn()
    _local_cache[key] = (fresh, time.time() + ttl, False)

@app.get("/analytics/summary")
async def analytics_summary(background_tasks: BackgroundTasks):
    key = "analytics:summary"
    now = time.time()

    if key in _local_cache:
        value, expires_at, is_refreshing = _local_cache[key]

        if now < expires_at:
            return value   # fresh — serve immediately

        if not is_refreshing:
            # Stale — serve old data, refresh in background
            _local_cache[key] = (value, expires_at, True)
            background_tasks.add_task(background_refresh, key, db.compute_analytics, 120)

        return value   # serve stale while refreshing

    # Cold start — must wait
    fresh = await db.compute_analytics()
    _local_cache[key] = (fresh, now + 120, False)
    return fresh
```

---

### 2.21 Stale-While-Revalidate (SWR) Pattern in FastAPI

> FastAPI signals SWR behavior to CDNs and browsers via `Cache-Control` headers. The CDN does the revalidation — not your server. This offloads all cache refresh work to the edge.

**Technologies:** `FastAPI`, `Cloudflare`, `AWS CloudFront`

```python
@app.get("/api/homepage-data")
async def homepage_data():
    data = await db.fetch_homepage_data()
    return JSONResponse(
        content=data,
        headers={
            # CDN serves stale for 30s while revalidating with origin
            # Origin is hit at most once per 30s, regardless of traffic
            "Cache-Control": "public, max-age=60, stale-while-revalidate=30",
            "Vary": "Accept-Encoding"
        }
    )
```

**SWR with `stale-if-error` for resilience:**

```python
@app.get("/api/product-feed")
async def product_feed():
    feed = await fetch_product_feed()
    return JSONResponse(
        content=feed,
        headers={
            # If origin is DOWN, serve stale for up to 1 hour
            "Cache-Control": "public, max-age=300, stale-while-revalidate=60, stale-if-error=3600"
        }
    )
```

> **💡 1% Tip:** `stale-if-error` is the most underused `Cache-Control` directive. It acts as an automatic CDN fallback when your origin crashes — users see cached responses instead of 500 errors. Always set it on public endpoints.

---

### 2.22 Rate Limiting with Redis (Cache-Adjacent)

> Rate limiting is not caching but uses the same Redis infrastructure. Essential for protecting high-value cached endpoints from abuse.

**Technologies:** `Redis`, `slowapi`, `Redis INCR`

**Fixed window rate limiter:**

```python
async def check_rate_limit(redis, identifier: str, limit: int, window: int) -> bool:
    """Fixed window: max `limit` requests per `window` seconds.
    NOTE: This is a fixed window counter — it resets hard at window boundaries.
    For a true sliding window, use a Redis Sorted Set of request timestamps."""
    now = int(time.time() * 1000)
    key = f"rl:{identifier}:{now // (window * 1000)}"

    pipe = redis.pipeline()
    pipe.incr(key)
    pipe.expire(key, window)
    results = await pipe.execute()

    return results[0] <= limit

@app.get("/api/expensive-endpoint")
async def expensive(request: Request, redis=Depends(get_redis)):
    ip = request.client.host
    if not await check_rate_limit(redis, ip, limit=100, window=60):
        raise HTTPException(429, "Rate limit exceeded")

    return await cached_expensive_operation()
```

**`slowapi` integration (declarative):**

```python
# pip install slowapi

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, storage_uri="redis://localhost:6379")
app.state.limiter = limiter

@app.get("/api/search")
@limiter.limit("30/minute")
async def search(request: Request, q: str):
    return await cached_search(q)
```

---

### 2.23 Probabilistic Early Expiry (XFetch Algorithm)

> Instead of waiting for TTL to hit zero (causing a stampede), start refreshing probabilistically **before** expiry. The closer to expiry a key is, the higher the refresh probability. Invented by A. Vattani et al. (VLDB 2015).

```
P(refresh) = [ -delta × beta × ln(rand()) ] > remaining_TTL
```

**Technologies:** `Redis`, `Python math`

```python
import math, random, time, json

BETA = 1.0   # higher = more aggressive pre-fetching

async def xfetch_get(redis, key: str, fetch_fn, ttl: int):
    raw = await redis.get(f"xf:{key}")

    if raw:
        data = json.loads(raw)
        remaining_ttl = await redis.ttl(f"xf:{key}")
        delta = data.get("_compute_time", 0.001)   # seconds the compute takes

        # XFetch decision: refresh early?
        should_refresh = -delta * BETA * math.log(random.random()) > remaining_ttl

        if not should_refresh:
            return data["value"]
        # else: fall through to recompute

    start = time.time()
    value = await fetch_fn()
    compute_time = time.time() - start

    await redis.setex(f"xf:{key}", ttl, json.dumps({
        "value": value,
        "_compute_time": compute_time   # stored for adaptive future decisions
    }))
    return value

@app.get("/heavy-report")
async def heavy_report():
    return await xfetch_get(
        redis,
        "report:annual",
        lambda: db.generate_annual_report(),
        ttl=300
    )
```

> **💡 1% Tip:** XFetch adapts automatically to computation cost. A function taking 2s to compute has ~37% probability of early refresh at 2 seconds remaining, and ~63% at 1 second remaining (BETA=1). A fast 10ms operation only triggers early refresh in the last ~10ms. Expected early refresh window ≈ `delta × beta` seconds before expiry.

---

### 2.24 Cache Key Design — The Art Nobody Talks About

> Cache key design is the most underrated topic in caching. Poor design causes collisions, wasted memory, unmanageable invalidation, and hard-to-reproduce bugs.

**Technologies:** `Redis`, `Memcached` (applies to all caches)

#### The 5 Rules

**Rule 1: Use Namespaces (colon-separated hierarchy)**

```
user:123:profile
user:123:orders
product:456:details
search:category:electronics:page:2
```

**Rule 2: Include Every Dimension That Affects the Result**

```
config:en_US            ← language matters
schema:v3:user:123      ← version matters
tenant:acme:user:123    ← tenant matters
```

**Rule 3: Hash Complex Parameters**

```python
import hashlib, json

def make_search_key(params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True)
    return "search:" + hashlib.md5(canonical.encode()).hexdigest()[:12]

# Bad:  search:{"q":"laptop","min_price":500,"max_price":1500,"page":2}
# Good: search:a3f9b2c1ef12
```

**Rule 4: Version Your Keys for Zero-Downtime Deploys**

```python
# When you change data format, bump the version.
# Old instances use v1, new instances use v2 — no conflicts, no corruption.
CACHE_VERSION = "v2"
key = f"{CACHE_VERSION}:user:{user_id}"
```

**Rule 5: Keep Keys Short — Memory Matters at Scale**

```
1 million keys × 40 bytes each = 40 MB just for keys

user:123       = 8 bytes   ✅
users/profile/details/123  = 26 bytes  ❌
```

**CacheKeyBuilder class:**

```python
class CacheKey:
    VERSION = "v2"

    @staticmethod
    def user(user_id: int, section: str = "profile") -> str:
        return f"{CacheKey.VERSION}:usr:{user_id}:{section}"

    @staticmethod
    def search(params: dict) -> str:
        slug = hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()[:10]
        return f"{CacheKey.VERSION}:srch:{slug}"

    @staticmethod
    def tenant_resource(tenant: str, resource: str, id: int) -> str:
        return f"{CacheKey.VERSION}:t:{tenant}:{resource}:{id}"

# Usage
key = CacheKey.user(123, "orders")               # v2:usr:123:orders
key = CacheKey.search({"q": "laptop", "p": 2})  # v2:srch:a3f9b2c1ab
```

---

## Quick Reference — Choosing a Strategy

### Frontend

| Use Case | Strategy |
|----------|----------|
| Static assets (JS/CSS/images) | `Cache-Control: immutable` + content-hashed filenames |
| Public HTML pages | CDN caching + `stale-while-revalidate` |
| Per-user HTML pages | `Cache-Control: private` + short `max-age` + ETags |
| API responses (JS client) | SWR pattern + Service Worker cache |
| Expensive Flask renders | Flask-Caching `@cached` + template fragments |
| Large local data | IndexedDB with TTL check |

### Backend (FastAPI)

| Use Case | Strategy |
|----------|----------|
| Config / rarely changes | `lru_cache` + Redis, long TTL + startup warming |
| User profiles / session data | Cache-Aside + Redis, medium TTL |
| Heavy DB aggregates | Write-Through + background refresh |
| Real-time with resilience | SWR headers + `stale-if-error` |
| High write throughput | Write-Behind + Redis Streams |
| Many instances, hot data | Two-level cache (L1 + L2) |
| Expensive computation | XFetch (probabilistic early expiry) |
| Unknown item existence | Bloom filter + negative caching |
| Cold starts / traffic spikes | Cache warming + circuit breaker + jitter TTL |
| Tag-based invalidation | Redis Sets as tag index |

---

## Libraries & Tools Cheat Sheet

### Frontend

| Library | Purpose |
|---------|---------|
| **Workbox** | Service Worker caching — declarative, production-ready |
| **idb** | Tiny IndexedDB wrapper with promise API |
| **SWR** | React hook: stale-while-revalidate pattern |
| **TanStack Query** | Full async state + cache management for React/Vue/Svelte |
| **Flask-Assets** | Asset pipeline + fingerprinting for Flask |
| **Flask-Caching** | View/function/fragment caching for Flask |

### Backend (FastAPI)

| Library | Purpose |
|---------|---------|
| **redis-py** | Official Redis client (sync + async) |
| **aioredis** | Async Redis — now merged into redis-py |
| **aiocache** | Async-native, multi-backend cache decorators |
| **cachetools** | `TTLCache`, `LRUCache`, etc. — in-process |
| **dogpile.cache** | ORM-level caching with SQLAlchemy |
| **fastapi-cache2** | FastAPI response cache decorator (Redis/Memcached/dict) |
| **slowapi** | Rate limiting for FastAPI (uses Redis) |
| **APScheduler** | Background job scheduler (proactive cache warming) |

### Infrastructure

| Tool | Purpose |
|------|---------|
| **Redis** | Primary cache backend — always choose Redis for production |
| **Valkey** | Redis fork (post-license change) — drop-in replacement |
| **Memcached** | Simpler, pure-cache use cases, high throughput |
| **Varnish** | HTTP accelerator / reverse proxy with ESI support |
| **Cloudflare** | CDN + edge caching + cache tag purging |
| **AWS CloudFront** | CDN with `s-maxage` + Lambda@Edge cache logic |
| **Fastly** | CDN with Varnish + VCL for custom cache logic |

---

*Written for the 1% who build truly scalable systems.*
