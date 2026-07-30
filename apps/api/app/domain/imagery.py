"""Binding a calibration to the imagery it was traced on.

A calibration is a set of pixel coordinates. Pixels only mean something relative
to one specific raster, so a calibration is only valid while the raster it was
traced on is the raster being served. Nothing enforced that, and the cost was
the bug this module exists to prevent: the committed vertices were digitised on
*Esri* imagery re-projected onto Google's grid, and when live Google imagery was
finally served the overlay sat about a metre and a half off the roof. Every
number downstream was still computed correctly - from the wrong outline.

Two signatures, because two different things can drift.

**The request signature** covers what we ask Google for: centre, zoom, size,
scale, map type, and the raster geometry that follows from them. It is a strict
equality check. If any of it changes, the calibration's pixels describe a
different grid and are simply wrong - there is no tolerance to apply.

**The imagery signature** covers what Google actually returns. The request can
be word-for-word correct and the imagery still be a different acquisition: a new
flyover, a different season, a more oblique view. That is precisely how the
original bug hid, so it is checked on content rather than on configuration.

The content check is perceptual, not exact. A byte hash would fail on any
re-encode - Google returning a visually identical PNG through a different
compressor would read as "the roof has moved", which is both wrong and the kind
of false alarm that gets a check disabled. So the byte hash is kept for
diagnostics only and the decision is made on a perceptual hash with a Hamming
threshold.

Measured on the real service while diagnosing this: two fetches of the same tile
differ by **0** bits, while the Esri fixture and the live Google raster differ by
**22 of 64**. The threshold sits far below that gap.
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import numpy as np
from PIL import Image

#: The fields that define which ground the raster covers, and at what scale.
#:
#: Everything here is either sent to Google or derived from what is sent. A
#: change to any of them relocates or rescales the grid the calibration's pixels
#: are expressed in.
REQUEST_SIGNATURE_FIELDS = (
    "center_latitude",
    "center_longitude",
    "zoom",
    "requested_width_px",
    "requested_height_px",
    "scale",
    "map_type",
    "source_width_px",
    "source_height_px",
    "ground_m_per_source_px",
)

#: Side of the square the raster is reduced to before hashing.
_HASH_GRID_PX = 32

#: Low-frequency coefficients kept. 8x8 = 64 bits.
_HASH_BLOCK = 8

#: How many of those 64 bits may differ before the imagery counts as changed.
#:
#: Two fetches of the same tile measured 0 bits apart; a different provider's
#: capture of the same ground measured 22. Eight sits clear of re-encoding noise
#: and far below a genuine change of imagery.
IMAGERY_HASH_MAX_DISTANCE = 8


@dataclass(frozen=True)
class ImageryVerdict:
    """Whether the served raster is the one the calibration was traced on."""

    matches: bool
    distance: int
    expected: str
    actual: str
    reason: str | None = None


#: Google's own Static Maps host.
#:
#: Imagery from anywhere else is a stub. That is a legitimate configuration for
#: a test stack and never a legitimate one for a proposal, so the distinction is
#: made here, once, rather than by a mode flag that has to be kept in step with
#: reality by hand.
GOOGLE_STATIC_HOST = "maps.googleapis.com"


def is_google_endpoint(url: str) -> bool:
    """Does this URL point at Google's own Static Maps service?"""
    return urlparse(url).hostname == GOOGLE_STATIC_HOST


def request_signature(config: Any) -> str:
    """A stable hash of the request configuration.

    Rounded before hashing: `ground_m_per_source_px` is a float derived through
    trigonometry, and an exact comparison of it would make the signature depend
    on the platform's last bit. Nine decimal places is far finer than any change
    that could move a pixel.
    """
    payload = {}
    for field in REQUEST_SIGNATURE_FIELDS:
        value = getattr(config, field)
        payload[field] = round(value, 9) if isinstance(value, float) else value
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def describe_request(config: Any) -> dict[str, Any]:
    """The signed fields, for an error that says *what* diverged."""
    return {
        field: (round(v, 9) if isinstance(v := getattr(config, field), float) else v)
        for field in REQUEST_SIGNATURE_FIELDS
    }


def imagery_sha256(data: bytes) -> str:
    """Exact hash of the raster bytes. Diagnostics only - never a gate.

    Recorded because when something does go wrong it answers "is this the very
    same file?" instantly. It must not decide anything: Google re-encoding an
    identical view would change it, and treating that as a moved roof would be a
    false alarm that teaches everyone to ignore the check.
    """
    return hashlib.sha256(data).hexdigest()


def perceptual_hash(data: bytes) -> str:
    """A 64-bit perceptual hash of a raster, as 16 hex characters.

    Low-frequency structure only - the layout of roofs, roads and shadows -
    which is what survives re-encoding and what changes when the imagery is
    genuinely re-flown.

    The DC coefficient is neutralised before thresholding. It encodes overall
    brightness, so leaving it in would let a slightly darker rendering of the
    same ground flip bits for a reason that has nothing to do with geometry.
    """
    grey = Image.open(io.BytesIO(data)).convert("L")
    grey = grey.resize((_HASH_GRID_PX, _HASH_GRID_PX), Image.Resampling.LANCZOS)

    spectrum = np.abs(np.fft.rfft2(np.asarray(grey, dtype=float)))
    block = spectrum[:_HASH_BLOCK, :_HASH_BLOCK].astype(float).copy()

    rest = np.delete(block.flatten(), 0)
    block[0, 0] = float(np.median(rest))

    flat = block.flatten()
    bits = flat > np.median(flat)

    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def hamming_distance(a: str, b: str) -> int:
    """How many bits two perceptual hashes differ in."""
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError as exc:
        raise ValueError(f"not a hexadecimal hash: {a!r} / {b!r}") from exc


def verify_imagery(
    data: bytes, *, expected_hash: str | None, max_distance: int = IMAGERY_HASH_MAX_DISTANCE
) -> ImageryVerdict:
    """Is this raster the one the calibration was traced on?

    An absent expectation is reported as *not* matching. A calibration that
    never recorded what it was traced against cannot vouch for anything, and
    treating silence as agreement is how the original bug survived review.
    """
    actual = perceptual_hash(data)
    if not expected_hash:
        return ImageryVerdict(
            matches=False,
            distance=-1,
            expected="",
            actual=actual,
            reason="the calibration does not record which imagery it was traced on",
        )

    distance = hamming_distance(expected_hash, actual)
    if distance <= max_distance:
        return ImageryVerdict(
            matches=True, distance=distance, expected=expected_hash, actual=actual
        )

    return ImageryVerdict(
        matches=False,
        distance=distance,
        expected=expected_hash,
        actual=actual,
        reason=(
            f"the imagery differs from the calibration by {distance} of 64 bits "
            f"(threshold {max_distance}); it has probably been re-flown"
        ),
    )
