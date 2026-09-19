"""JPEG/PNG decode with an explicit pixel cap applied before `load()`.

Pillow's process-global `MAX_IMAGE_PIXELS` is not the Stage 6 control: its
default is larger than 4096x4096, and a warning can pass silently. This
wrapper converts decompression-bomb warnings to errors for the duration of
`open()`, checks `width * height` against 16_777_216, and only then loads
pixels. Programming errors (`MemoryError`, `TypeError`, …) are not mapped
to a merchant-image failure.
"""

from __future__ import annotations

import warnings
from io import BytesIO
from typing import Final

from PIL import Image, UnidentifiedImageError

from app.ai.image_fetch import ImageFetchDecodeFailed, ImageFetchPixelLimit

MAX_PIXELS: Final[int] = 16_777_216

# Closed input-failure family for JPEG/PNG identification and payload decode.
# UnidentifiedImageError subclasses OSError; both are listed so the catch is
# explicit. Do not add Exception, TypeError, RuntimeError, MemoryError,
# AssertionError, ValueError, or SyntaxError.
_DECODE_INPUT_ERRORS = (UnidentifiedImageError, OSError)


def decode_image(body: bytes) -> Image.Image:
    """Open JPEG/PNG bytes. Pixel cap is enforced before any raster load."""
    # Do not mutate process-global Image.MAX_IMAGE_PIXELS.
    # Leave ImageFile.LOAD_TRUNCATED_IMAGES at Pillow's default (False).
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            image = Image.open(BytesIO(body))
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ImageFetchPixelLimit from exc
        except _DECODE_INPUT_ERRORS as exc:
            raise ImageFetchDecodeFailed from exc
    width, height = image.size
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        raise ImageFetchPixelLimit
    try:
        image.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageFetchPixelLimit from exc
    except _DECODE_INPUT_ERRORS as exc:
        raise ImageFetchDecodeFailed from exc
    if image.format not in {"JPEG", "PNG"}:
        raise ImageFetchDecodeFailed
    return image


def persisted_format(image: Image.Image) -> str:
    """Pillow's `JPEG`/`PNG` → the JSON `decodedFormat` token."""
    if image.format == "JPEG":
        return "jpeg"
    if image.format == "PNG":
        return "png"
    raise ImageFetchDecodeFailed


__all__ = ["MAX_PIXELS", "decode_image", "persisted_format"]
