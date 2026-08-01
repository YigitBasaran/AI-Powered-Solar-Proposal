"""Binding a calibration to the imagery it was traced on.

The bug these exist for: the committed roof vertices were traced on Esri
imagery re-projected onto Google's grid, and when live Google imagery was
finally served the overlay sat about 1.2 m off the roof. Every number
downstream computed cleanly - from the wrong outline. Nothing failed, because
nothing was checking.

Two independent things can drift, so there are two checks and they are tested
separately: the *request* (which ground, at what scale) and the *imagery*
(which acquisition of it).
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from app.core.config import get_settings
from app.core.errors import RoofCalibrationMismatchError
from app.domain.imagery import (
    IMAGERY_HASH_MAX_DISTANCE,
    hamming_distance,
    imagery_sha256,
    perceptual_hash,
    request_signature,
    verify_imagery,
)
from app.services.roof import assert_calibration_matches, calibration_metadata, load_calibration


def _raster(seed: int, size: int = 256, *, tint: int = 0) -> bytes:
    rng = np.random.default_rng(seed)
    pixels = rng.integers(40, 200, size=(size, size, 3), dtype=np.uint8)
    if tint:
        pixels = np.clip(pixels.astype(int) + tint, 0, 255).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(pixels, mode="RGB").save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# The request signature: strict, and it names what moved
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"zoom": 19},
        {"scale": 1},
        {"center_latitude": -34.05},
        {"center_longitude": 18.47},
        {"map_type": "hybrid"},
        {"requested_width_px": 512},
    ],
)
def test_any_change_to_the_request_changes_the_signature(override) -> None:
    cfg = get_settings().satellite_image_config
    assert request_signature(cfg.model_copy(update=override)) != request_signature(cfg)


def test_the_signature_is_stable_across_processes() -> None:
    """It is compared against a value committed to a file, so it must not drift."""
    cfg = get_settings().satellite_image_config
    assert request_signature(cfg) == request_signature(cfg.model_copy())


def test_a_changed_zoom_that_keeps_the_width_is_still_caught() -> None:
    """The blind spot in the guard this replaced.

    The old check compared `width_px` only. Halving the zoom while doubling the
    requested size leaves the raster 1280 px wide and covering four times the
    ground - every stored pixel then means something different, and it sailed
    straight through.
    """
    data = load_calibration()
    cfg = get_settings().satellite_image_config
    disguised = cfg.model_copy(update={"zoom": 19, "requested_width_px": 1280, "scale": 1})
    assert disguised.source_width_px == 1280

    with pytest.raises(RoofCalibrationMismatchError) as caught:
        assert_calibration_matches(data, disguised)
    assert "zoom" in str(caught.value.details["diverged"])


def test_the_error_names_the_fields_that_diverged() -> None:
    data = load_calibration()
    cfg = get_settings().satellite_image_config
    with pytest.raises(RoofCalibrationMismatchError) as caught:
        assert_calibration_matches(data, cfg.model_copy(update={"zoom": 18}))

    diverged = caught.value.details["diverged"]
    assert "zoom" in diverged
    assert diverged["zoom"] == {"calibration": 20, "configured": 18}


def test_a_calibration_without_a_signature_is_refused() -> None:
    """Absent provenance is not agreement.

    This is exactly the state the Esri-derived file was in: it recorded nothing
    about what it was traced against, so nothing could tell that it no longer
    described the imagery being served.
    """
    cfg = get_settings().satellite_image_config
    with pytest.raises(RoofCalibrationMismatchError, match="does not record"):
        assert_calibration_matches({"vertices": []}, cfg)


def test_the_committed_calibration_matches_the_configured_request() -> None:
    assert_calibration_matches(load_calibration(), get_settings().satellite_image_config)


# ---------------------------------------------------------------------------
# The imagery signature: perceptual, because a re-encode is not a moved roof
# ---------------------------------------------------------------------------


def test_the_same_raster_hashes_identically() -> None:
    assert perceptual_hash(_raster(1)) == perceptual_hash(_raster(1))


def test_re_encoding_does_not_read_as_changed_imagery() -> None:
    """The reason the byte hash cannot be the gate.

    Google re-encoding a visually identical tile must not be reported as the
    roof having moved - that is a false alarm, and false alarms are how a check
    ends up disabled.
    """
    original = _raster(2)
    reopened = io.BytesIO()
    Image.open(io.BytesIO(original)).save(reopened, format="PNG", optimize=True)
    requantised = reopened.getvalue()

    assert imagery_sha256(requantised) != imagery_sha256(original), "not a real re-encode"
    assert hamming_distance(perceptual_hash(original), perceptual_hash(requantised)) <= (
        IMAGERY_HASH_MAX_DISTANCE
    )


def test_a_brightness_shift_does_not_read_as_changed_imagery() -> None:
    """Why the DC coefficient is neutralised before thresholding."""
    assert (
        hamming_distance(perceptual_hash(_raster(3)), perceptual_hash(_raster(3, tint=25)))
        <= IMAGERY_HASH_MAX_DISTANCE
    )


def test_different_ground_reads_as_changed_imagery() -> None:
    assert (
        hamming_distance(perceptual_hash(_raster(4)), perceptual_hash(_raster(5)))
        > IMAGERY_HASH_MAX_DISTANCE
    )


def test_verification_refuses_when_the_calibration_records_no_imagery() -> None:
    verdict = verify_imagery(_raster(6), expected_hash=None)
    assert verdict.matches is False
    assert "does not record" in (verdict.reason or "")


def test_verification_accepts_the_imagery_it_was_traced_on() -> None:
    raster = _raster(7)
    verdict = verify_imagery(raster, expected_hash=perceptual_hash(raster))
    assert verdict.matches is True
    assert verdict.distance == 0


def test_verification_reports_the_distance_when_it_refuses() -> None:
    verdict = verify_imagery(_raster(8), expected_hash=perceptual_hash(_raster(9)))
    assert verdict.matches is False
    assert verdict.distance > IMAGERY_HASH_MAX_DISTANCE
    assert "of 64 bits" in (verdict.reason or "")


def test_the_committed_calibration_records_both_signatures() -> None:
    """A calibration that cannot vouch for its imagery is not usable evidence."""
    meta = calibration_metadata(load_calibration())
    assert meta["request_signature"]
    assert meta["imagery"]["perceptual_hash"]
    assert meta["imagery"]["sha256"]
    assert meta["traced_on"]


def test_the_committed_calibration_is_verified_not_provisional() -> None:
    """A calibration is not authoritative until a human has looked at it.

    The re-traced vertices were derived by registering the previous tracing onto
    the Google raster and were marked `provisional` while that fit was only
    machine-checked. Every figure this system publishes rests on those six
    points, so "a person compared the outline with the roof" is a fact worth
    recording in the file and worth refusing to ship without.
    """
    meta = calibration_metadata(load_calibration())

    assert meta["status"] == "verified", (
        f"the committed calibration is {meta['status']!r}. Review the overlay "
        "against the live raster and mark it verified before relying on it."
    )
    assert meta["verified_on"]
    assert meta["verified_by"]


def test_the_re_trace_changed_placement_and_nothing_measurable() -> None:
    """A pure translation moves the outline without moving any measurement.

    This is what made the re-trace safe to accept: no length, area, azimuth,
    panel count, production figure or financial result depends on where the
    raster's origin happens to be, so correcting the placement could not
    silently alter a quoted number.
    """
    registration = calibration_metadata(load_calibration())["registration"]

    assert registration["rotation_deg"] == 0.0
    assert registration["shift_m"] < 2.0, "a larger shift would deserve a fresh tracing"
