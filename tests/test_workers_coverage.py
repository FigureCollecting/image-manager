"""Tests to raise workers/tasks.py coverage to ≥85%.

Covers: _ext_for_mime, _final_key, verify_and_register_object (happy path,
sha mismatch, missing image), _apply_transforms (resize, crop, blur, formats),
create_transformed_version, generate_album_cover.
"""

from __future__ import annotations

import io
from contextlib import contextmanager
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from app.models import (
    Album,
    AlbumItem,
    ImageVersion,
    UserImageLink,
)
from app.models import (
    Image as ImageModel,
)
from app.workers.tasks import (
    _apply_transforms,
    _ext_for_mime,
    _final_key,
    create_transformed_version,
    generate_album_cover,
    verify_and_register_object,
)
from PIL import Image
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _make_image_bytes(width: int = 8, height: int = 8, fmt: str = "PNG") -> bytes:
    """Create minimal image bytes in memory."""
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _fake_worker_session(db_session: Session):
    """Return a context manager factory that yields the test db_session."""

    @contextmanager
    def _ctx(session_factory=None):
        yield db_session

    return _ctx


class TestExtForMime:
    def test_jpeg(self) -> None:
        assert _ext_for_mime("image/jpeg") == "jpg"

    def test_jpg(self) -> None:
        assert _ext_for_mime("image/jpg") == "jpg"

    def test_png(self) -> None:
        assert _ext_for_mime("image/png") == "png"

    def test_webp(self) -> None:
        assert _ext_for_mime("image/webp") == "webp"

    def test_tiff(self) -> None:
        assert _ext_for_mime("image/tiff") == "tif"

    def test_unknown_uses_mimetypes(self) -> None:
        result = _ext_for_mime("image/gif")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_totally_unknown_falls_back(self) -> None:
        result = _ext_for_mime("application/x-unknown-type-12345")
        assert result == "bin"


class TestFinalKey:
    def test_format(self) -> None:
        sha = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        assert _final_key(sha, "png") == f"ab/cd/{sha}.png"

    def test_short_sha(self) -> None:
        assert _final_key("abcd", "jpg") == "ab/cd/abcd.jpg"


class TestVerifyAndRegisterObject:
    def test_happy_path(self, db_session: Session) -> None:
        """Verify, compute metadata, move to final key, create v1, update links."""
        data = _make_image_bytes(16, 16)
        from app.hashing import sha256_bytes

        sha = sha256_bytes(data)

        img = ImageModel(sha256=sha, storage_key="staging/test.png")
        db_session.add(img)
        db_session.flush()

        link = UserImageLink(
            user_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            image_id=img.id,
            current_version_id=None,
            role="owner",
        )
        db_session.add(link)
        db_session.flush()

        mock_body = MagicMock()
        mock_body.read.return_value = data
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": mock_body, "ContentType": "image/png"}

        with (
            patch("app.workers.tasks.get_s3", return_value=mock_s3),
            patch("app.workers.tasks.move_object") as mock_move,
            patch("app.workers.tasks.worker_session", _fake_worker_session(db_session)),
        ):
            verify_and_register_object(img.id, "images", "staging/test.png", sha)

        # Image updated
        assert img.width == 16
        assert img.height == 16
        assert img.phash is not None
        assert img.mime == "image/png"

        # v1 created
        v1 = db_session.execute(
            select(ImageVersion).where(
                ImageVersion.image_id == img.id, ImageVersion.version_no == 1
            )
        ).scalar_one()
        assert v1.visibility == "private"
        assert v1.width == 16

        # Link updated (check directly, no refresh needed since same session)
        assert link.current_version_id == v1.id

        mock_move.assert_called_once()

    def test_sha_mismatch_returns_early(self, db_session: Session) -> None:
        data = _make_image_bytes()
        img = ImageModel(sha256="expected_sha", storage_key="staging/test.png")
        db_session.add(img)
        db_session.flush()

        mock_body = MagicMock()
        mock_body.read.return_value = data
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": mock_body}

        with (
            patch("app.workers.tasks.get_s3", return_value=mock_s3),
            patch("app.workers.tasks.move_object") as mock_move,
        ):
            verify_and_register_object(img.id, "images", "staging/test.png", "wrong_sha")

        mock_move.assert_not_called()

    def test_missing_image_returns_early(self, db_session: Session) -> None:
        data = _make_image_bytes()
        from app.hashing import sha256_bytes

        sha = sha256_bytes(data)

        mock_body = MagicMock()
        mock_body.read.return_value = data
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": mock_body, "ContentType": "image/png"}

        with (
            patch("app.workers.tasks.get_s3", return_value=mock_s3),
            patch("app.workers.tasks.move_object"),
            patch("app.workers.tasks.worker_session", _fake_worker_session(db_session)),
        ):
            verify_and_register_object(9999, "images", "staging/test.png", sha)

        versions = db_session.execute(select(ImageVersion)).scalars().all()
        assert len(versions) == 0

    def test_v1_already_exists_skips_creation(self, db_session: Session) -> None:
        data = _make_image_bytes(16, 16)
        from app.hashing import sha256_bytes

        sha = sha256_bytes(data)

        img = ImageModel(sha256=sha, storage_key="staging/test.png")
        db_session.add(img)
        db_session.flush()

        v1 = ImageVersion(
            image_id=img.id,
            version_no=1,
            storage_key="existing/key.png",
            visibility="private",
            age_rating=0,
        )
        db_session.add(v1)
        db_session.flush()

        mock_body = MagicMock()
        mock_body.read.return_value = data
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": mock_body, "ContentType": "image/png"}

        with (
            patch("app.workers.tasks.get_s3", return_value=mock_s3),
            patch("app.workers.tasks.move_object"),
            patch("app.workers.tasks.worker_session", _fake_worker_session(db_session)),
        ):
            verify_and_register_object(img.id, "images", "staging/test.png", sha)

        versions = (
            db_session.execute(select(ImageVersion).where(ImageVersion.image_id == img.id))
            .scalars()
            .all()
        )
        assert len(versions) == 1


class TestApplyTransforms:
    def test_resize_width_and_height(self) -> None:
        data = _make_image_bytes(64, 64)
        result, mime, w, h = _apply_transforms(data, {"resize": {"width": 32, "height": 16}})
        assert w == 32
        assert h == 16
        assert mime == "image/jpeg"

    def test_resize_width_only(self) -> None:
        data = _make_image_bytes(64, 32)
        result, mime, w, h = _apply_transforms(data, {"resize": {"width": 32}})
        assert w == 32
        assert h == 16

    def test_resize_height_only(self) -> None:
        data = _make_image_bytes(64, 32)
        result, mime, w, h = _apply_transforms(data, {"resize": {"height": 16}})
        assert w == 32
        assert h == 16

    def test_crop(self) -> None:
        data = _make_image_bytes(64, 64)
        result, mime, w, h = _apply_transforms(
            data, {"crop": {"x": 0, "y": 0, "width": 32, "height": 16}}
        )
        assert w == 32
        assert h == 16

    def test_blur(self) -> None:
        data = _make_image_bytes(32, 32)
        result, mime, w, h = _apply_transforms(data, {"blur": {"sigma": 5.0}})
        assert w == 32
        assert h == 32
        assert len(result) > 0

    def test_format_webp(self) -> None:
        data = _make_image_bytes(16, 16)
        result, mime, w, h = _apply_transforms(data, {"format": "WEBP"})
        assert mime == "image/webp"

    def test_format_png(self) -> None:
        data = _make_image_bytes(16, 16)
        result, mime, w, h = _apply_transforms(data, {"format": "PNG"})
        assert mime == "image/png"

    def test_format_jpg(self) -> None:
        data = _make_image_bytes(16, 16)
        result, mime, w, h = _apply_transforms(data, {"format": "JPG"})
        assert mime == "image/jpeg"

    def test_format_unknown_defaults_jpeg(self) -> None:
        data = _make_image_bytes(16, 16)
        result, mime, w, h = _apply_transforms(data, {"format": "BMP"})
        assert mime == "image/jpeg"

    def test_no_transforms(self) -> None:
        data = _make_image_bytes(16, 16)
        result, mime, w, h = _apply_transforms(data, {})
        assert w == 16
        assert h == 16

    def test_matte_produces_rgba_output_not_rgb(self) -> None:
        """CRITICAL: the matte transform must never .convert('RGB') --
        that would strip the alpha channel the rest of the pipeline
        (grounding scalars, serve route) depends on."""
        data = _make_image_bytes(32, 32, fmt="PNG")
        result, mime, w, h = _apply_transforms(data, {"matte": True})
        assert mime == "image/png"
        out_img = Image.open(io.BytesIO(result))
        assert out_img.mode == "RGBA"
        assert w == 32
        assert h == 32

    def test_matte_forces_alpha_capable_format_even_if_jpeg_requested(self) -> None:
        data = _make_image_bytes(16, 16)
        result, mime, w, h = _apply_transforms(data, {"matte": True, "format": "JPEG"})
        assert mime == "image/png"
        out_img = Image.open(io.BytesIO(result))
        assert out_img.mode == "RGBA"

    def test_matte_composes_with_resize(self) -> None:
        data = _make_image_bytes(64, 64, fmt="PNG")
        result, mime, w, h = _apply_transforms(
            data, {"matte": True, "resize": {"width": 16, "height": 16}}
        )
        assert w == 16
        assert h == 16
        assert mime == "image/png"
        out_img = Image.open(io.BytesIO(result))
        assert out_img.mode == "RGBA"

    def test_quality_parameter(self) -> None:
        data = _make_image_bytes(32, 32)
        result, mime, w, h = _apply_transforms(data, {"quality": 50})
        assert len(result) > 0


class TestCreateTransformedVersion:
    def test_happy_path(self, db_session: Session) -> None:
        img = ImageModel(sha256="abc123" + "0" * 58, storage_key="test/img.png")
        db_session.add(img)
        db_session.flush()

        base = ImageVersion(
            image_id=img.id,
            version_no=1,
            storage_key="test/v1.png",
            visibility="private",
            age_rating=0,
        )
        db_session.add(base)
        db_session.flush()

        new_v = ImageVersion(
            image_id=img.id,
            version_no=2,
            storage_key="test/v2.jpg",
            visibility="private",
            age_rating=0,
        )
        db_session.add(new_v)
        db_session.flush()

        src_data = _make_image_bytes(32, 32)
        mock_body = MagicMock()
        mock_body.read.return_value = src_data
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": mock_body}

        mock_settings = MagicMock()
        mock_settings.s3_bucket = "test-bucket"

        with (
            patch("app.workers.tasks.worker_session", _fake_worker_session(db_session)),
            patch("app.workers.tasks.get_s3", return_value=mock_s3),
            patch("app.config.get_settings", return_value=mock_settings),
        ):
            create_transformed_version(
                image_id=img.id,
                base_version_id=base.id,
                version_id=new_v.id,
                transform_spec={"resize": {"width": 16, "height": 16}},
                dest_key="test/v2.jpg",
                visibility="public",
                age_rating=0,
                alt_for_version_id=None,
            )

        assert new_v.width == 16
        assert new_v.height == 16
        assert new_v.visibility == "public"
        assert new_v.storage_key == "test/v2.jpg"
        mock_s3.put_object.assert_called_once()

    def test_missing_base_version_returns_early(self, db_session: Session) -> None:
        with patch("app.workers.tasks.worker_session", _fake_worker_session(db_session)):
            create_transformed_version(
                image_id=1,
                base_version_id=9999,
                version_id=1,
                transform_spec={},
                dest_key="test/v.jpg",
                visibility="private",
                age_rating=0,
                alt_for_version_id=None,
            )

    def test_missing_target_version_returns_early(self, db_session: Session) -> None:
        img = ImageModel(sha256="def456" + "0" * 58, storage_key="test/img2.png")
        db_session.add(img)
        db_session.flush()

        base = ImageVersion(
            image_id=img.id,
            version_no=1,
            storage_key="test/v1b.png",
            visibility="private",
            age_rating=0,
        )
        db_session.add(base)
        db_session.flush()

        src_data = _make_image_bytes(16, 16)
        mock_body = MagicMock()
        mock_body.read.return_value = src_data
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": mock_body}

        mock_settings = MagicMock()
        mock_settings.s3_bucket = "test-bucket"

        with (
            patch("app.workers.tasks.worker_session", _fake_worker_session(db_session)),
            patch("app.workers.tasks.get_s3", return_value=mock_s3),
            patch("app.config.get_settings", return_value=mock_settings),
        ):
            create_transformed_version(
                image_id=img.id,
                base_version_id=base.id,
                version_id=9999,
                transform_spec={},
                dest_key="test/v.jpg",
                visibility="private",
                age_rating=0,
                alt_for_version_id=None,
            )


class TestGenerateAlbumCover:
    def test_empty_album_produces_blank_canvas(self, db_session: Session) -> None:
        album = Album(title="Empty", default_visibility="private")
        db_session.add(album)
        db_session.flush()

        mock_s3 = MagicMock()
        mock_settings = MagicMock()
        mock_settings.s3_bucket = "test-bucket"

        with (
            patch("app.workers.tasks.worker_session", _fake_worker_session(db_session)),
            patch("app.workers.tasks.get_s3", return_value=mock_s3),
            patch("app.config.get_settings", return_value=mock_settings),
        ):
            result = generate_album_cover(album.id)

        assert result.startswith(f"albums/{album.id}/cover-")
        assert result.endswith(".webp")
        mock_s3.put_object.assert_called_once()

    def test_album_with_items_creates_mosaic(self, db_session: Session) -> None:
        album = Album(title="Gallery", default_visibility="private")
        db_session.add(album)
        db_session.flush()

        tile_data = _make_image_bytes(64, 64)
        for i in range(3):
            img = ImageModel(sha256=f"hash{i}" + "0" * 59, storage_key=f"img/{i}.png")
            db_session.add(img)
            db_session.flush()
            v = ImageVersion(
                image_id=img.id,
                version_no=1,
                storage_key=f"img/{i}/v1.png",
                visibility="private",
                age_rating=0,
            )
            db_session.add(v)
            db_session.flush()
            item = AlbumItem(album_id=album.id, position=i, image_id=img.id, version_id=v.id)
            db_session.add(item)

        db_session.flush()

        mock_body = MagicMock()
        mock_body.read.return_value = tile_data
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": mock_body}

        mock_settings = MagicMock()
        mock_settings.s3_bucket = "test-bucket"

        with (
            patch("app.workers.tasks.worker_session", _fake_worker_session(db_session)),
            patch("app.workers.tasks.get_s3", return_value=mock_s3),
            patch("app.config.get_settings", return_value=mock_settings),
        ):
            result = generate_album_cover(album.id)

        assert result.startswith(f"albums/{album.id}/cover-")
        assert result.endswith(".webp")
        mock_s3.put_object.assert_called_once()
        assert mock_s3.get_object.call_count == 3
