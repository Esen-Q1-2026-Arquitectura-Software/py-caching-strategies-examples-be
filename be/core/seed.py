from core.database import AsyncSessionLocal
from core.models import CatalogItem, Order, Track, UserProfile
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
