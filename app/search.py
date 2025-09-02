from __future__ import annotations

from typing import Iterable

from sqlalchemy import func, text
from sqlalchemy.sql.elements import ColumnElement


def tsvector_concat(*cols: ColumnElement) -> ColumnElement:
    return func.to_tsvector("simple", func.coalesce(func.concat_ws(" ", *cols), ""))


def ts_query(query: str) -> ColumnElement:
    return func.plainto_tsquery("simple", query)


def tags_any_match(col_tags: ColumnElement, tags: Iterable[str]) -> ColumnElement:
    # expects an array column; use overlap operator
    arr = func.ARRAY(list(tags))
    return col_tags.op("&&")(arr)

