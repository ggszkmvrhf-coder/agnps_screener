"""Thin, fail-soft access layer for PostGIS.

The backend must never crash because a layer is missing or the database is
unreachable. Every helper here returns an empty / falsy result and logs a
warning instead of raising, so the caller can record a human-readable note and
keep going with partial results.
"""
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Cache one engine per URL so repeated requests reuse the pool.
_engines: Dict[str, Engine] = {}


def get_engine(database_url: Optional[str]) -> Optional[Engine]:
    """Return a SQLAlchemy engine, or None if no URL is configured."""
    if not database_url:
        return None
    if database_url not in _engines:
        try:
            _engines[database_url] = create_engine(
                database_url, pool_pre_ping=True, future=True
            )
        except Exception as exc:  # bad URL / missing driver
            logger.warning("Could not create DB engine: %s", exc)
            return None
    return _engines[database_url]


def database_reachable(engine: Optional[Engine]) -> bool:
    if engine is None:
        return False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("Database not reachable: %s", exc)
        return False


def table_exists(engine: Optional[Engine], table_name: str) -> bool:
    """True if a table/view of this name exists (to_regclass is schema-aware)."""
    if engine is None:
        return False
    try:
        with engine.connect() as conn:
            res = conn.execute(
                text("SELECT to_regclass(:t)"), {"t": table_name}
            ).scalar()
            return res is not None
    except Exception as exc:
        logger.warning("table_exists(%s) failed: %s", table_name, exc)
        return False


def fetch_all(
    engine: Optional[Engine], sql: str, params: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Run a query and return a list of row dicts. Returns [] on any failure."""
    if engine is None:
        return []
    try:
        with engine.connect() as conn:
            res = conn.execute(text(sql), params or {})
            cols = list(res.keys())
            return [dict(zip(cols, row)) for row in res.fetchall()]
    except Exception as exc:
        logger.warning("query failed: %s\nSQL: %s", exc, sql)
        return []
