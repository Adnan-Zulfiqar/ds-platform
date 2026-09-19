"""Deterministic, model-free image checks: blur, byte-identical duplicates.

No provider import. Watermark detection is not implemented; callers persist
the N/A block rather than a false negative.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from typing import Final

from PIL import Image

WORKING_SIZE: Final[int] = 256
BLUR_THRESHOLD: Final[int] = 100
N: Final[int] = 254 * 254  # 64_516 interior samples
WATERMARK_NA: Final[dict[str, object]] = {
    "applicable": False,
    "reason": "genericWatermarkDetectionNotImplemented",
}


def normalize_working_canvas(
    image: Image.Image,
) -> tuple[Image.Image, int, int, int, int]:
    """Return a 256x256 L canvas and the paste geometry. Never upscales."""
    luma = image.convert("RGB").convert("L")
    width, height = luma.size
    if width > WORKING_SIZE or height > WORKING_SIZE:
        scale_den = max(width, height)
        new_w = max(1, (width * WORKING_SIZE) // scale_den)
        new_h = max(1, (height * WORKING_SIZE) // scale_den)
        luma = luma.resize((new_w, new_h), Image.Resampling.BILINEAR)
    else:
        new_w, new_h = width, height
    canvas = Image.new("L", (WORKING_SIZE, WORKING_SIZE), 128)
    left = (WORKING_SIZE - new_w) // 2
    top = (WORKING_SIZE - new_h) // 2
    canvas.paste(luma, (left, top))
    return canvas, new_w, new_h, left, top


def _luma_at(canvas: Image.Image, x: int, y: int) -> int:
    value = canvas.getpixel((x, y))
    if not isinstance(value, int):
        raise TypeError("working canvas is not single-channel luma")
    return value


def blur_score(canvas: Image.Image) -> int:
    """Population variance of the 3x3 Laplacian, integer round-half-up."""
    sum_l = 0
    sum_sq = 0
    for y in range(1, WORKING_SIZE - 1):
        for x in range(1, WORKING_SIZE - 1):
            sample = (
                _luma_at(canvas, x, y - 1)
                + _luma_at(canvas, x, y + 1)
                + _luma_at(canvas, x - 1, y)
                + _luma_at(canvas, x + 1, y)
                - 4 * _luma_at(canvas, x, y)
            )
            sum_l += sample
            sum_sq += sample * sample
    numerator = N * sum_sq - sum_l * sum_l
    denominator = N * N
    return (numerator + denominator // 2) // denominator


def is_blurry(score: int) -> bool:
    return score < BLUR_THRESHOLD


def content_sha256(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def duplicate_of_image_ids(
    members: Sequence[tuple[uuid.UUID, str]],
) -> dict[uuid.UUID, list[str]]:
    """Phase B: symmetric, lexicographically sorted sibling ids per hash."""
    groups: dict[str, list[uuid.UUID]] = {}
    for image_id, digest in members:
        groups.setdefault(digest, []).append(image_id)
    result: dict[uuid.UUID, list[str]] = {}
    for image_id, digest in members:
        others = [peer for peer in groups[digest] if peer != image_id]
        result[image_id] = sorted(str(peer) for peer in others)
    return result


__all__ = [
    "BLUR_THRESHOLD",
    "WATERMARK_NA",
    "WORKING_SIZE",
    "N",
    "blur_score",
    "content_sha256",
    "duplicate_of_image_ids",
    "is_blurry",
    "normalize_working_canvas",
]
