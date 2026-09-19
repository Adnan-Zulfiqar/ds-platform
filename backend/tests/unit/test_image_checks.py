"""Deterministic blur, normalisation geometry, and byte-identical duplicates."""

from __future__ import annotations

import uuid

import pytest
from PIL import Image

from app.ai.image_checks import (
    BLUR_THRESHOLD,
    WATERMARK_NA,
    blur_score,
    content_sha256,
    duplicate_of_image_ids,
    is_blurry,
    normalize_working_canvas,
)

pytestmark = pytest.mark.unit

GEOMETRY = (
    ((256, 256), 256, 256, 0, 0),
    ((512, 512), 256, 256, 0, 0),
    ((500, 200), 256, 102, 0, 77),
    ((200, 500), 102, 256, 77, 0),
    ((128, 128), 128, 128, 64, 64),
    ((128, 512), 64, 256, 96, 0),
    ((512, 128), 256, 64, 0, 96),
    ((301, 500), 154, 256, 51, 0),
    ((500, 301), 256, 154, 0, 51),
)


def _solid(width: int, height: int, value: int = 40) -> Image.Image:
    return Image.new("RGB", (width, height), (value, value, value))


def _luma_from_fn(fn: object) -> Image.Image:
    image = Image.new("L", (256, 256))
    pixels = image.load()
    for y in range(256):
        for x in range(256):
            pixels[x, y] = fn(x, y)  # type: ignore[operator]
    return image


def _box15(source: Image.Image) -> Image.Image:
    area = 225
    out = Image.new("L", (256, 256))
    src = source.load()
    dst = out.load()
    for y in range(256):
        for x in range(256):
            total = 0
            for dy in range(-7, 8):
                yy = min(max(y + dy, 0), 255)
                for dx in range(-7, 8):
                    xx = min(max(x + dx, 0), 255)
                    total += int(src[xx, yy])
            dst[x, y] = total // area
    return out


class TestNormalisation:
    @pytest.mark.parametrize(
        ("source", "new_w", "new_h", "left", "top"),
        GEOMETRY,
    )
    def test_geometry(
        self,
        source: tuple[int, int],
        new_w: int,
        new_h: int,
        left: int,
        top: int,
    ) -> None:
        canvas, got_w, got_h, got_left, got_top = normalize_working_canvas(_solid(*source))
        assert (got_w, got_h, got_left, got_top) == (new_w, new_h, left, top)
        assert canvas.size == (256, 256)
        pixels = canvas.load()
        if left > 0:
            assert pixels[0, 128] == 128
        if top > 0:
            assert pixels[128, 0] == 128
        if left + new_w < 256:
            assert pixels[255, min(255, top + new_h // 2)] == 128
        if top + new_h < 256:
            assert pixels[min(255, left + new_w // 2), 255] == 128


class TestBlur:
    def test_uniform_128(self) -> None:
        canvas = Image.new("L", (256, 256), 128)
        score = blur_score(canvas)
        assert score == 0
        assert is_blurry(score) is True

    def test_checkerboard_1px(self) -> None:
        canvas = _luma_from_fn(lambda x, y: 255 if (x + y) % 2 else 0)
        score = blur_score(canvas)
        assert score == 1_040_400
        assert is_blurry(score) is False

    def test_checkerboard_16px(self) -> None:
        canvas = _luma_from_fn(lambda x, y: 255 if ((x // 16) + (y // 16)) % 2 else 0)
        score = blur_score(canvas)
        assert score == 17_174
        assert is_blurry(score) is False

    def test_checkerboard_16px_box15(self) -> None:
        sharp = _luma_from_fn(lambda x, y: 255 if ((x // 16) + (y // 16)) % 2 else 0)
        blurred = _box15(sharp)
        score = blur_score(blurred)
        assert score == 37
        assert is_blurry(score) is True

    @pytest.mark.parametrize(
        ("score", "expected"),
        [(0, True), (37, True), (99, True), (100, False), (17174, False), (1040400, False)],
    )
    def test_threshold_boundary(self, score: int, expected: bool) -> None:
        assert is_blurry(score) is expected
        assert BLUR_THRESHOLD == 100


class TestDuplicates:
    def test_two_identical_are_symmetric(self) -> None:
        a = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        b = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        digest = content_sha256(b"same")
        result = duplicate_of_image_ids(((a, digest), (b, digest)))
        assert result[a] == [str(b)]
        assert result[b] == [str(a)]
        reversed_result = duplicate_of_image_ids(((b, digest), (a, digest)))
        assert reversed_result[a] == [str(b)]
        assert reversed_result[b] == [str(a)]

    def test_three_identical_list_the_other_two_sorted(self) -> None:
        a = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        b = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        c = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
        digest = content_sha256(b"same")
        result = duplicate_of_image_ids(((a, digest), (b, digest), (c, digest)))
        assert result[a] == [str(b), str(c)]
        assert result[b] == [str(a), str(c)]
        assert result[c] == [str(a), str(b)]

    def test_different_bytes_have_empty_lists(self) -> None:
        a = uuid.uuid4()
        b = uuid.uuid4()
        result = duplicate_of_image_ids(((a, content_sha256(b"one")), (b, content_sha256(b"two"))))
        assert result[a] == []
        assert result[b] == []

    def test_lone_success_has_empty_duplicate_list(self) -> None:
        a = uuid.uuid4()
        result = duplicate_of_image_ids(((a, content_sha256(b"only")),))
        assert result[a] == []

    def test_watermark_na_block(self) -> None:
        assert WATERMARK_NA == {
            "applicable": False,
            "reason": "genericWatermarkDetectionNotImplemented",
        }
