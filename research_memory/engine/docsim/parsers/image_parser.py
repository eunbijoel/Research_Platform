"""Image bytes helpers for Hangul HWPX parse (embedded pictures)."""
import io
from typing import Optional

from PIL import Image


def bytes_to_image(image_bytes: bytes) -> Optional[Image.Image]:
    """Open image bytes as a PIL Image. Return None when the payload is corrupt."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        return img
    except Exception:  # noqa: BLE001
        return None
