from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement


def is_postgres(db: Session) -> bool:
    """Return True if the session is backed by PostgreSQL."""
    return db.bind.dialect.name == "postgresql"  # type: ignore[union-attr]


def tsvector_concat(*cols: ColumnElement) -> ColumnElement:
    return func.to_tsvector("simple", func.coalesce(func.concat_ws(" ", *cols), ""))


def ts_query(query: str) -> ColumnElement:
    return func.plainto_tsquery("simple", query)


def build_text_filter(db: Session, query: str, *cols: ColumnElement) -> ColumnElement:
    """Build a text-search filter clause.

    On PostgreSQL: uses to_tsvector / plainto_tsquery for proper full-text search.
    On other engines (SQLite, etc.): falls back to ILIKE substring matching.
    """
    if is_postgres(db):
        vector = tsvector_concat(*cols)
        return vector.op("@@")(ts_query(query))
    # Fallback: ILIKE on each column
    pattern = f"%{query}%"
    return or_(*(col.ilike(pattern) for col in cols))


def tags_any_match(col_tags: ColumnElement, tags: Iterable[str]) -> ColumnElement:
    # expects an array column; use overlap operator
    arr = func.ARRAY(list(tags))
    return col_tags.op("&&")(arr)
