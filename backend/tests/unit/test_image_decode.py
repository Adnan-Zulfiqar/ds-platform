"""Decode contract: pixel cap before load, closed exception mapping."""

from __future__ import annotations

import struct
import zlib
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock

import pytest
from PIL import Image

from app.ai.image_decode import MAX_PIXELS, decode_image, persisted_format
from app.ai.image_fetch import ImageFetchDecodeFailed, ImageFetchPixelLimit

pytestmark = pytest.mark.unit


def _png_bytes(width: int, height: int, *, fill: int = 128) -> bytes:
    image = Image.new("RGB", (width, height), (fill, fill, fill))
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(width: int, height: int, *, fill: int = 128) -> bytes:
    image = Image.new("RGB", (width, height), (fill, fill, fill))
    buf = BytesIO()
    image.save(buf, format="JPEG")
    return buf.getvalue()


def _png_ihdr_only(width: int, height: int) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", crc)
    iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
    return signature + ihdr + iend


class TestDecodeFormats:
    def test_valid_png(self) -> None:
        image = decode_image(_png_bytes(32, 24))
        assert image.size == (32, 24)
        assert persisted_format(image) == "png"

    def test_valid_jpeg(self) -> None:
        image = decode_image(_jpeg_bytes(32, 24))
        assert image.size == (32, 24)
        assert persisted_format(image) == "jpeg"

    def test_truncated_png_maps_to_decode_failed(self) -> None:
        with pytest.raises(ImageFetchDecodeFailed):
            decode_image(b"\x89PNG\r\n\x1a\n\x00\x00")

    def test_truncated_jpeg_maps_to_decode_failed(self) -> None:
        with pytest.raises(ImageFetchDecodeFailed):
            decode_image(b"\xff\xd8\xff")

    def test_unidentified_bytes_map_to_decode_failed(self) -> None:
        with pytest.raises(ImageFetchDecodeFailed):
            decode_image(b"not-an-image")


class TestPixelCap:
    def test_4096_square_policy_allows_without_allocation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = MagicMock()
        fake.size = (4096, 4096)
        fake.format = "PNG"
        fake.load.return_value = None
        monkeypatch.setattr(Image, "open", lambda *_args, **_kwargs: fake)

        image = decode_image(b"ignored")

        assert image is fake
        fake.load.assert_called_once()
        assert 4096 * 4096 == MAX_PIXELS

    def test_4097x4096_rejected_before_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = MagicMock()
        fake.size = (4097, 4096)
        fake.format = "PNG"
        monkeypatch.setattr(Image, "open", lambda *_args, **_kwargs: fake)

        with pytest.raises(ImageFetchPixelLimit):
            decode_image(b"ignored")
        fake.load.assert_not_called()

    def test_forged_huge_ihdr_is_pixel_limit(self) -> None:
        body = _png_ihdr_only(100_000, 100_000)
        with pytest.raises(ImageFetchPixelLimit):
            decode_image(body)

    def test_decompression_bomb_warning_during_open_maps(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(*_args: Any, **_kwargs: Any) -> Image.Image:
            raise Image.DecompressionBombWarning("too many pixels")

        monkeypatch.setattr(Image, "open", explode)
        with pytest.raises(ImageFetchPixelLimit):
            decode_image(b"ignored")


class TestUnexpectedErrorsPropagate:
    def test_memory_error_from_load_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = MagicMock()
        fake.size = (16, 16)
        fake.format = "PNG"
        fake.load.side_effect = MemoryError("boom")
        monkeypatch.setattr(Image, "open", lambda *_args, **_kwargs: fake)

        with pytest.raises(MemoryError):
            decode_image(b"ignored")

    def test_type_error_from_load_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = MagicMock()
        fake.size = (16, 16)
        fake.format = "PNG"
        fake.load.side_effect = TypeError("boom")
        monkeypatch.setattr(Image, "open", lambda *_args, **_kwargs: fake)

        with pytest.raises(TypeError):
            decode_image(b"ignored")
