from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Image(Base):
    __tablename__ = "images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    phash: Mapped[str | None] = mapped_column(String(64))
    mime: Mapped[str | None] = mapped_column(String(100))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    bytes: Mapped[int | None] = mapped_column(BigInteger)
    storage_key: Mapped[str | None] = mapped_column(String(512), unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC), nullable=False
    )
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    versions: Mapped[list[ImageVersion]] = relationship(
        back_populates="image", cascade="all, delete-orphan"
    )


class ImageVersion(Base):
    __tablename__ = "image_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    image_id: Mapped[int] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    derived_from_version: Mapped[int | None] = mapped_column(Integer)
    transform_spec: Mapped[dict] = mapped_column(JSON, default=dict)
    mime: Mapped[str | None] = mapped_column(String(100))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    bytes: Mapped[int | None] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    visibility: Mapped[str] = mapped_column(String(16), default="private", nullable=False)
    age_rating: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    alt_for_version_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC), nullable=False
    )
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("image_id", "version_no", name="uq_image_version_no"),
        CheckConstraint(
            "visibility in ('private','tenant','public','catalog')", name="ck_visibility"
        ),
    )

    image: Mapped[Image] = relationship(back_populates="versions")


class UserImageLink(Base):
    __tablename__ = "user_image_links"

    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    image_id: Mapped[int] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"), primary_key=True
    )
    current_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("image_versions.id", ondelete="SET NULL")
    )
    role: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC), nullable=False
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    actor_service: Mapped[str | None] = mapped_column(String(100))
    tenant_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    image_id: Mapped[int | None] = mapped_column(Integer)
    version_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC), nullable=False
    )


class Album(Base):
    __tablename__ = "albums"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    owner_user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    default_visibility: Mapped[str] = mapped_column(String(16), default="private", nullable=False)
    is_shareable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_item_override: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC), nullable=False
    )
    share_token_hash: Mapped[str | None] = mapped_column(String(128))
    share_age_threshold: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "default_visibility in ('private','tenant','public')", name="ck_album_visibility"
        ),
    )


class AlbumItem(Base):
    __tablename__ = "album_items"

    album_id: Mapped[int] = mapped_column(
        ForeignKey("albums.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    image_id: Mapped[int] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int | None] = mapped_column(Integer)
    item_visibility: Mapped[str | None] = mapped_column(String(16))


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    owner_user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))

    __table_args__ = (CheckConstraint("scope in ('global','tenant','user')", name="ck_tag_scope"),)


class ImageTag(Base):
    __tablename__ = "image_tags"

    image_id: Mapped[int] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default="00000000-0000-0000-0000-000000000000"
    )
    owner_user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))


class AlbumTag(Base):
    __tablename__ = "album_tags"

    album_id: Mapped[int] = mapped_column(
        ForeignKey("albums.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class ExternalRef(Base):
    __tablename__ = "external_refs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ref_type: Mapped[str] = mapped_column(String(50), nullable=False)
    ref_id: Mapped[str] = mapped_column(String(200), nullable=False)
    image_id: Mapped[int] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int | None] = mapped_column(Integer)
    tenant_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))

    __table_args__ = (UniqueConstraint("ref_type", "ref_id", name="uq_ref_type_id"),)


class ServiceClient(Base):
    __tablename__ = "service_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False)


# Useful indexes
Index("ix_image_versions_image_id_visibility", ImageVersion.image_id, ImageVersion.visibility)
Index("ix_album_title_trgm", Album.title)
Index("ix_album_desc_trgm", Album.description)
