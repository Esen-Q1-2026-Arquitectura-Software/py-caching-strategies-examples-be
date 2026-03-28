from core.database import Base
from sqlalchemy import JSON, Column, Float, Integer, String


class Order(Base):
    """Orders table — source of truth for the 2.3 Cache-Aside demo."""

    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    customer = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    amount = Column(Float, nullable=False)
    items = Column(JSON, nullable=False)


class UserProfile(Base):
    """User profiles table — source of truth for the 2.4 Write-Through demo."""

    __tablename__ = "user_profiles"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(200), nullable=False)
    bio = Column(String(500), nullable=False)
    preferences = Column(JSON, nullable=False)


class EventLog(Base):
    """Analytics event log — persisted by the 2.5 Write-Behind background worker."""

    __tablename__ = "event_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(50), nullable=False)
    user_id = Column(Integer, nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(Float, nullable=False)


class CatalogItem(Base):
    """Catalog items — source of truth for the 2.6 Read-Through demo."""

    __tablename__ = "catalog_items"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    category = Column(String(80), nullable=False)
    description = Column(String(500), nullable=False)
    price = Column(Float, nullable=False)


class Track(Base):
    """Music tracks — source of truth for the 2.7 Response Caching Middleware demo."""

    __tablename__ = "tracks"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    artist = Column(String(100), nullable=False)
    album = Column(String(200), nullable=False)
    genre = Column(String(50), nullable=False)
    duration_s = Column(Integer, nullable=False)


class NewsArticle(Base):
    """News articles — source of truth for the 2.8 HTTP Caching (ETag/Cache-Control) demo."""

    __tablename__ = "news_articles"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    summary = Column(String(500), nullable=False)
    body = Column(String(2000), nullable=False)
    author = Column(String(100), nullable=False)
    category = Column(String(50), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    updated_at = Column(
        Float, nullable=False
    )  # Unix timestamp — used for Last-Modified


class Recipe(Base):
    """Recipes table — source of truth for the 2.9 Async aiocache demo."""

    __tablename__ = "recipes"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    cuisine = Column(String(80), nullable=False)
    prep_minutes = Column(Integer, nullable=False)
    ingredients = Column(JSON, nullable=False)
    instructions = Column(String(1000), nullable=False)


class Employee(Base):
    """Employees table — source of truth for the 2.10 DB Query Caching demo."""

    __tablename__ = "employees"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    department = Column(String(80), nullable=False)
    role = Column(String(100), nullable=False)
    salary = Column(Integer, nullable=False)
    hire_year = Column(Integer, nullable=False)
    active = Column(Integer, nullable=False, default=1)  # 1=active, 0=inactive
