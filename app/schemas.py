from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class DevTokenRequest(BaseModel):
    user_id: str | None = None
    tenant_id: str | None = None
    aud: str | None = Field(default=None, description="service:<name> for service tokens")


class DevTokenResponse(BaseModel):
    token: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class RefreshTokenResponse(BaseModel):
    access_token: str
    refresh_token: str


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


class InitiateUploadRequest(BaseModel):
    filename: str
    mime: str
    size: int = Field(gt=0)


class InitiateUploadResponse(BaseModel):
    model_config = {"extra": "allow"}

    url: str
    fields: dict[str, str]
    staging_key: str
    bucket: str


class CompleteUploadRequest(BaseModel):
    sha256: str = Field(min_length=64, max_length=64)
    key: str
    mime: str
    size: int = Field(gt=0)


class CompleteUploadResponse(BaseModel):
    image_id: int
    created: bool


class VersionSummary(BaseModel):
    id: int
    version_no: int
    visibility: str
    age_rating: int
    alt_for_version_id: int | None = None
    mime: str | None = None
    width: int | None = None
    height: int | None = None
    bytes: int | None = None


class ImageDetailResponse(BaseModel):
    id: int
    sha256: str
    mime: str | None = None
    bytes: int | None = None
    width: int | None = None
    height: int | None = None
    versions: list[VersionSummary] = []


class ImageVersionResponse(BaseModel):
    image_id: int
    version_id: int


class CreateVersionRequest(BaseModel):
    transform_spec: dict[str, Any] = Field(default_factory=dict)
    base_version_id: int | None = None
    visibility: Literal["private", "tenant", "public", "catalog"] = "private"
    age_rating: int = 0
    create_safe_alt_for: int | None = None


class CreateVersionResponse(BaseModel):
    # No storage_key here: internal object keys stay internal. Clients
    # address versions via /serve/{image_id}@{version_id}.
    version_id: int
    version_no: int


class SetVisibilityRequest(BaseModel):
    visibility: Literal["private", "tenant", "public", "catalog"]


class OkResponse(BaseModel):
    ok: bool


class ExposeSafeAltRequest(BaseModel):
    age_rating: int = 0
    blur_spec: dict[str, Any] | None = None


class ExposeSafeAltResponse(BaseModel):
    version_id: int
    alt_for: int


# ---------------------------------------------------------------------------
# Albums
# ---------------------------------------------------------------------------


class CreateAlbumRequest(BaseModel):
    title: str
    description: str | None = None
    default_visibility: Literal["private", "tenant", "public"] = "private"
    is_shareable: bool = False
    share_age_threshold: int = 0


class CreateAlbumResponse(BaseModel):
    id: int


class UpdateAlbumRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    default_visibility: Literal["private", "tenant", "public"] | None = None
    is_shareable: bool | None = None
    share_age_threshold: int | None = None


class AddAlbumItemRequest(BaseModel):
    image_id: int
    version_id: int | None = None
    position: int | None = None


class AddAlbumItemResponse(BaseModel):
    position: int


class ReorderItem(BaseModel):
    from_position: int
    to_position: int


class ReorderRequest(BaseModel):
    items: list[ReorderItem] = []


class AlbumItemSummary(BaseModel):
    position: int
    image_id: int
    version_id: int | None = None


class AlbumDetailResponse(BaseModel):
    id: int
    title: str
    description: str | None = None
    default_visibility: str
    is_shareable: bool
    share_age_threshold: int
    items: list[AlbumItemSummary] = []


class ShareAlbumRequest(BaseModel):
    enable: bool
    share_age_threshold: int | None = None


class ShareAlbumResponse(BaseModel):
    share_url: str | None = None


class AlbumCoverResponse(BaseModel):
    # TODO(C1): storage_key exposes an internal object key, but today it is
    # the ONLY pointer a client has to the (public-read, async-generated)
    # cover object -- and the value returned is a "cover-pending" placeholder
    # that doesn't even match the final content-hashed key. Replace with a
    # served URL once the grant model lands.
    storage_key: str


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


class CreateTagRequest(BaseModel):
    name: str
    scope: Literal["global", "tenant", "user"]
    tenant_id: str | None = None


class CreateTagResponse(BaseModel):
    id: int
    name: str


class TagItemsRequest(BaseModel):
    tag_ids: list[int] = []
    names: list[str] = []


class TagItemsResponse(BaseModel):
    count: int


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class ImageSearchResult(BaseModel):
    id: int
    mime: str | None = None
    sha256: str


class ImageSearchResponse(BaseModel):
    results: list[ImageSearchResult]
    next_cursor: int | None = None


class AlbumSearchResult(BaseModel):
    id: int
    title: str


class AlbumSearchResponse(BaseModel):
    results: list[AlbumSearchResult]
    next_cursor: int | None = None


# ---------------------------------------------------------------------------
# External refs
# ---------------------------------------------------------------------------


class CreateExternalRefRequest(BaseModel):
    ref_type: str
    ref_id: str
    image_id: int
    version_id: int | None = None


class CreateExternalRefResponse(BaseModel):
    id: int


class ExternalAssetResponse(BaseModel):
    image_id: int
    version_id: int
    mime: str | None = None
    width: int | None = None
    height: int | None = None
    url: str


# ---------------------------------------------------------------------------
# Figure galleries
# ---------------------------------------------------------------------------


class GalleryImageItem(BaseModel):
    url: str
    position: int
    caption: str | None = None


class IngestGalleryRequest(BaseModel):
    figureId: str
    images: list[GalleryImageItem] = []


class IngestGalleryResponse(BaseModel):
    figureId: str
    imagesQueued: int
    duplicatesSkipped: int


class GalleryImageSummary(BaseModel):
    id: int
    url: str
    position: int
    caption: str | None = None


class GalleryDetailResponse(BaseModel):
    figureId: str
    images: list[GalleryImageSummary] = []
    count: int


class ReorderGalleryRequest(BaseModel):
    imageIds: list[int] = []


# ---------------------------------------------------------------------------
# Display + grounding metadata (image-manager -> fc-mobile contract)
#
# Field names/nesting here are the FROZEN cross-service contract mirrored by
# fc-shared's FigureDisplayMeta (fc-shared/src/types/index.ts) -- do not
# rename/retype without updating both sides. In particular, contact_band_*
# is stored as FLAT columns on ImageVersion but must be served NESTED here.
# ---------------------------------------------------------------------------


class ContactBandResponse(BaseModel):
    centerXFrac: float
    widthFrac: float


class DisplayMetaResponse(BaseModel):
    matted: bool = False
    matteImageId: str | None = None
    matteVersionId: str | None = None
    bottomMarginFrac: float | None = None
    contactBand: ContactBandResponse | None = None
    thumbhash: str | None = None
    dominantColor: str | None = None
