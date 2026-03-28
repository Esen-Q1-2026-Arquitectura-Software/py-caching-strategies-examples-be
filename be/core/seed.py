from core.database import AsyncSessionLocal
from core.models import CatalogItem, NewsArticle, Order, Recipe, Track, UserProfile
from sqlalchemy import select

_SEED_ORDERS = [
    {
        "id": 1,
        "customer": "Alice Johnson",
        "status": "delivered",
        "amount": 299.99,
        "items": [
            {"sku": "LAP-001", "name": "Laptop Pro 15", "qty": 1, "price": 299.99}
        ],
    },
    {
        "id": 2,
        "customer": "Bob Smith",
        "status": "processing",
        "amount": 42.98,
        "items": [
            {"sku": "MSE-001", "name": "Wireless Mouse", "qty": 1, "price": 29.99},
            {"sku": "NTB-001", "name": "Notebook Pack", "qty": 1, "price": 12.99},
        ],
    },
    {
        "id": 3,
        "customer": "Carol White",
        "status": "pending",
        "amount": 1449.98,
        "items": [
            {"sku": "DSK-001", "name": "Standing Desk", "qty": 1, "price": 449.99},
            {"sku": "LAP-001", "name": "Laptop Pro 15", "qty": 2, "price": 999.99},
        ],
    },
    {
        "id": 4,
        "customer": "David Lee",
        "status": "shipped",
        "amount": 89.99,
        "items": [{"sku": "COF-001", "name": "Coffee Maker", "qty": 1, "price": 89.99}],
    },
    {
        "id": 5,
        "customer": "Eve Martinez",
        "status": "delivered",
        "amount": 25.98,
        "items": [
            {"sku": "NTB-001", "name": "Notebook Pack", "qty": 1, "price": 12.99},
            {"sku": "PEN-001", "name": "Gel Pen Set", "qty": 1, "price": 12.99},
        ],
    },
]

_SEED_PROFILES = [
    {
        "id": 1,
        "name": "Alice Johnson",
        "email": "alice@example.com",
        "bio": "Software engineer, coffee enthusiast.",
        "preferences": {"theme": "dark", "language": "en", "notifications": True},
    },
    {
        "id": 2,
        "name": "Bob Smith",
        "email": "bob@example.com",
        "bio": "UX designer, typography nerd.",
        "preferences": {"theme": "light", "language": "fr", "notifications": False},
    },
    {
        "id": 3,
        "name": "Carol White",
        "email": "carol@example.com",
        "bio": "DevOps engineer, automation fan.",
        "preferences": {"theme": "dark", "language": "de", "notifications": True},
    },
    {
        "id": 4,
        "name": "David Lee",
        "email": "david@example.com",
        "bio": "Backend developer, open source contributor.",
        "preferences": {"theme": "light", "language": "ja", "notifications": True},
    },
    {
        "id": 5,
        "name": "Eve Martinez",
        "email": "eve@example.com",
        "bio": "Full-stack developer, digital nomad.",
        "preferences": {"theme": "dark", "language": "es", "notifications": False},
    },
]

_SEED_CATALOG = [
    {
        "id": 1,
        "title": "Python Distributed Systems",
        "category": "Book",
        "description": "Deep dive into building scalable services with Python.",
        "price": 39.99,
    },
    {
        "id": 2,
        "title": "Redis in Action",
        "category": "Book",
        "description": "Practical Redis patterns and real-world production use cases.",
        "price": 34.99,
    },
    {
        "id": 3,
        "title": "Designing Data-Intensive Applications",
        "category": "Book",
        "description": "The definitive guide to modern data systems at scale.",
        "price": 44.99,
    },
    {
        "id": 4,
        "title": "FastAPI Course — Pro Bundle",
        "category": "Course",
        "description": "Full production FastAPI development from zero to deployment.",
        "price": 79.00,
    },
    {
        "id": 5,
        "title": "Caching Patterns Handbook",
        "category": "eBook",
        "description": "Patterns from LRU to Read-Through explained with real examples.",
        "price": 19.99,
    },
    {
        "id": 6,
        "title": "SQLAlchemy Async Mastery",
        "category": "Course",
        "description": "Async ORM patterns for high-concurrency FastAPI applications.",
        "price": 59.00,
    },
]

_SEED_TRACKS = [
    {
        "id": 1,
        "title": "Bohemian Rhapsody",
        "artist": "Queen",
        "album": "A Night at the Opera",
        "genre": "Rock",
        "duration_s": 354,
    },
    {
        "id": 2,
        "title": "Hotel California",
        "artist": "Eagles",
        "album": "Hotel California",
        "genre": "Rock",
        "duration_s": 391,
    },
    {
        "id": 3,
        "title": "Lose Yourself",
        "artist": "Eminem",
        "album": "8 Mile Soundtrack",
        "genre": "Hip-Hop",
        "duration_s": 326,
    },
    {
        "id": 4,
        "title": "Billie Jean",
        "artist": "Michael Jackson",
        "album": "Thriller",
        "genre": "Pop",
        "duration_s": 294,
    },
    {
        "id": 5,
        "title": "So What",
        "artist": "Miles Davis",
        "album": "Kind of Blue",
        "genre": "Jazz",
        "duration_s": 562,
    },
]

# 2025-03-23 … 2025-03-27 00:00:00 UTC  (one article per day, stable for reproducible ETags)
_SEED_ARTICLES = [
    {
        "id": 1,
        "title": "Why ETags Are the Unsung Hero of HTTP Performance",
        "summary": "ETags enable conditional requests that return 304 Not Modified when content is unchanged — saving every byte of the response body.",
        "body": (
            "An ETag (Entity Tag) is an HTTP response header that acts as a unique fingerprint "
            "for a resource. When a client makes a subsequent request for the same resource, it "
            "includes the cached ETag in an If-None-Match header. If the server's current ETag "
            "matches, it returns a 304 Not Modified response with NO body — saving bandwidth "
            "entirely. On a high-traffic API serving 1 KB responses to 10 000 req/s, eliminating "
            "even 50 % of body transfers saves 5 GB/s of egress. ETags are computed from a "
            "deterministic hash (SHA-256 or MD5) of the serialised resource. They change "
            "automatically whenever the underlying data changes."
        ),
        "author": "Alice Chen",
        "category": "Backend",
        "version": 1,
        "updated_at": 1743033600.0,
    },
    {
        "id": 2,
        "title": "Cache-Control Directives: A Complete Field Guide",
        "summary": "max-age, s-maxage, immutable, stale-while-revalidate — each directive serves a distinct purpose. Picking the wrong one wastes bandwidth or breaks freshness.",
        "body": (
            "Cache-Control is the primary HTTP/1.1 mechanism for controlling caching behaviour. "
            "max-age=N tells the browser to serve from its own cache for N seconds. s-maxage=N "
            "overrides max-age for shared caches (CDNs) only. no-cache means always revalidate "
            "with the server before serving — not 'never cache'. no-store truly prevents storage. "
            "immutable tells browsers never to revalidate within the max-age window, even on "
            "hard refresh — ideal for hashed asset filenames. stale-while-revalidate=N allows "
            "serving a stale response while a background fetch refreshes the cache. Combining "
            "these directives for different resource types (HTML, API, static assets) is the "
            "key skill that separates junior from senior developers."
        ),
        "author": "Bob Schmidt",
        "category": "HTTP",
        "version": 1,
        "updated_at": 1742947200.0,
    },
    {
        "id": 3,
        "title": "Conditional Requests: If-None-Match vs If-Modified-Since",
        "summary": "Two mechanisms, one goal: avoid retransmitting bytes you already have. ETags win on precision; Last-Modified wins on simplicity.",
        "body": (
            "HTTP conditional requests let clients validate their cached copy without downloading "
            "the full resource again. If-None-Match uses an ETag fingerprint: if the server "
            "resource has the same hash, it responds with 304 and no body. If-Modified-Since "
            "uses a timestamp: if the resource has not changed since that datetime, same 304 "
            "response. ETags are more precise — two resources can have the same Last-Modified "
            "but different content (e.g. race conditions, sub-second edits). The server MUST "
            "include ETag and/or Last-Modified in the original 200 response for the client to "
            "use in subsequent conditional requests. FastAPI makes this trivial: set the "
            "headers in your JSONResponse and read them back from request.headers on the next call."
        ),
        "author": "Carol White",
        "category": "HTTP",
        "version": 1,
        "updated_at": 1742860800.0,
    },
    {
        "id": 4,
        "title": "Immutable Assets: The Cache-Control Superpower",
        "summary": "Content-addressed filenames + Cache-Control: immutable = zero revalidation requests. The single highest-impact cache optimisation for frontend performance.",
        "body": (
            "When a file's content is baked into its URL (e.g. app.a3f9b2c1.js), the content "
            "can never change at that URL. Setting Cache-Control: public, max-age=31536000, "
            "immutable tells the browser: cache forever, never ask the server again. The "
            "'immutable' extension prevents even the hard-refresh revalidation request that "
            "would normally occur. Combined with a CDN, this means static assets cost zero "
            "origin requests after the first visit. The only rollout concern: purging the "
            "CDN cache when you deploy is unnecessary because the new build produces new "
            "hashed filenames automatically. Old filenames stay cached; new filenames are "
            "fetched fresh. Zero-downtime, zero-stale-content deployments."
        ),
        "author": "David Kim",
        "category": "Frontend",
        "version": 1,
        "updated_at": 1742774400.0,
    },
    {
        "id": 5,
        "title": "List-Level ETags: Invalidating Collections Correctly",
        "summary": "A list endpoint needs its own ETag that changes when ANY item in the list changes. Hashing a fingerprint list is the production pattern.",
        "body": (
            "Individual resource ETags are straightforward. List ETags are trickier. The ETag "
            "for GET /articles must change whenever ANY article changes — not just the ones "
            "currently on the page. The production pattern: compute the ETag from a tuple of "
            "(id, version) for every item in the list, sorted by id. A SHA-256 hash of that "
            "serialised list becomes the list ETag. When any article is updated, its version "
            "increments, the fingerprint list changes, the list ETag changes, and the next "
            "conditional GET returns 200 with the full updated list. This gives O(1) "
            "conditional check (a single Redis GET or DB query for versions only) at the "
            "cost of a slightly more expensive ETag computation on the server."
        ),
        "author": "Eve Martinez",
        "category": "Backend",
        "version": 1,
        "updated_at": 1742688000.0,
    },
]

_SEED_RECIPES = [
    {
        "id": 1,
        "name": "Classic Margherita Pizza",
        "cuisine": "Italian",
        "prep_minutes": 20,
        "ingredients": [
            "400g pizza dough",
            "150ml tomato sauce",
            "200g mozzarella",
            "fresh basil",
            "2 tbsp olive oil",
        ],
        "instructions": "Preheat oven to 220°C. Roll dough thin, spread sauce, add torn mozzarella. Bake 12 min until golden. Top with fresh basil.",
    },
    {
        "id": 2,
        "name": "Chicken Tikka Masala",
        "cuisine": "Indian",
        "prep_minutes": 35,
        "ingredients": [
            "500g chicken breast",
            "200ml tikka paste",
            "400ml coconut cream",
            "1 onion",
            "2 garlic cloves",
            "fresh coriander",
        ],
        "instructions": "Marinate chicken in tikka paste 30 min. Fry onion and garlic, add chicken, pour in coconut cream. Simmer 20 min. Garnish with coriander.",
    },
    {
        "id": 3,
        "name": "Beef Ramen",
        "cuisine": "Japanese",
        "prep_minutes": 45,
        "ingredients": [
            "200g ramen noodles",
            "400ml beef broth",
            "100g beef strips",
            "soft-boiled egg",
            "nori",
            "spring onions",
            "2 tbsp soy sauce",
        ],
        "instructions": "Simmer broth with soy sauce. Cook noodles per pack. Pan-fry beef strips 3 min. Assemble bowl: noodles, broth, beef, egg, nori.",
    },
    {
        "id": 4,
        "name": "Greek Salad",
        "cuisine": "Greek",
        "prep_minutes": 10,
        "ingredients": [
            "2 tomatoes",
            "1 cucumber",
            "100g feta",
            "kalamata olives",
            "red onion",
            "olive oil",
            "dried oregano",
        ],
        "instructions": "Chop tomatoes, cucumber, and onion. Combine in bowl. Add olives and crumbled feta. Drizzle olive oil, sprinkle oregano. Serve immediately.",
    },
    {
        "id": 5,
        "name": "Pad Thai",
        "cuisine": "Thai",
        "prep_minutes": 25,
        "ingredients": [
            "200g rice noodles",
            "200g shrimp",
            "2 eggs",
            "bean sprouts",
            "spring onions",
            "peanuts",
            "3 tbsp pad thai sauce",
        ],
        "instructions": "Soak noodles 15 min. Stir-fry shrimp 2 min. Push aside, scramble eggs. Add noodles and sauce, toss together. Top with sprouts and peanuts.",
    },
    {
        "id": 6,
        "name": "Shakshuka",
        "cuisine": "Middle Eastern",
        "prep_minutes": 25,
        "ingredients": [
            "400g canned tomatoes",
            "4 eggs",
            "1 bell pepper",
            "1 onion",
            "2 garlic cloves",
            "1 tsp cumin",
            "1 tsp paprika",
            "fresh parsley",
        ],
        "instructions": "Sauté onion, garlic, and pepper. Add tomatoes, cumin, paprika. Simmer 10 min. Crack eggs into sauce, cover, cook 7 min. Garnish with parsley.",
    },
    {
        "id": 7,
        "name": "French Onion Soup",
        "cuisine": "French",
        "prep_minutes": 55,
        "ingredients": [
            "4 large onions",
            "1L beef broth",
            "100ml white wine",
            "baguette slices",
            "150g Gruyère",
            "2 tbsp butter",
            "1 tsp thyme",
        ],
        "instructions": "Caramelize onions in butter 40 min. Add wine and reduce. Pour in broth, simmer 15 min. Ladle into bowls, top with baguette and Gruyère. Grill until bubbly.",
    },
    {
        "id": 8,
        "name": "Chocolate Lava Cake",
        "cuisine": "French",
        "prep_minutes": 20,
        "ingredients": [
            "200g dark chocolate",
            "100g butter",
            "4 eggs",
            "80g sugar",
            "50g plain flour",
            "cocoa powder for dusting",
        ],
        "instructions": "Melt chocolate and butter. Whisk eggs and sugar until pale, fold in chocolate mixture. Add flour. Pour into greased ramekins. Bake 12 min at 200°C.",
    },
]


async def seed_database() -> None:
    async with AsyncSessionLocal() as session:
        if (await session.execute(select(Order))).scalars().first() is None:
            for row in _SEED_ORDERS:
                session.add(Order(**row))
            for row in _SEED_PROFILES:
                session.add(UserProfile(**row))
            await session.commit()
            print("DB seeded with orders and user profiles.")

        if (await session.execute(select(CatalogItem))).scalars().first() is None:
            for row in _SEED_CATALOG:
                session.add(CatalogItem(**row))
            await session.commit()
            print("DB seeded with catalog items.")

        if (await session.execute(select(Track))).scalars().first() is None:
            for row in _SEED_TRACKS:
                session.add(Track(**row))
            await session.commit()
            print("DB seeded with tracks.")

        if (await session.execute(select(NewsArticle))).scalars().first() is None:
            for row in _SEED_ARTICLES:
                session.add(NewsArticle(**row))
            await session.commit()
            print("DB seeded with news articles.")

        if (await session.execute(select(Recipe))).scalars().first() is None:
            for row in _SEED_RECIPES:
                session.add(Recipe(**row))
            await session.commit()
            print("DB seeded with recipes.")
