"""Configuration tests.

The headline one is `.env.example`: copying it to `.env` is the first line of
the README's quick start, so if it does not load, nothing works — including
`docker compose up`, which reads the same file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import REPO_ROOT, Settings, meters_per_source_pixel

ENV_EXAMPLE = REPO_ROOT / ".env.example"


def test_env_example_exists() -> None:
    assert ENV_EXAMPLE.is_file(), "the quick start depends on this file"


def test_env_example_loads_without_error(tmp_path: Path) -> None:
    """Regression: `SMTP_PORT=` (blank) used to fail integer validation.

    A template ships blank placeholders for optional settings — that is how it
    says "fill this in if you need it". An empty numeric value must therefore
    fall back to its default rather than take the application down on the very
    first step of the setup instructions.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")

    settings = Settings(_env_file=str(env_file))  # type: ignore[call-arg]

    assert settings.smtp_port == 0
    assert settings.google_maps_api_key == ""
    assert settings.case_resolved_latitude == pytest.approx(-34.04658242871865)


def test_every_blank_value_in_env_example_is_accepted(tmp_path: Path) -> None:
    """No blank placeholder anywhere in the template may break loading."""
    blanks = [
        line.split("=", 1)[0]
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.strip().startswith("#") and line.split("=", 1)[1] == ""
    ]
    assert blanks, "expected the template to carry blank placeholders"

    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{key}=" for key in blanks), encoding="utf-8")
    Settings(_env_file=str(env_file))  # type: ignore[call-arg]


def test_blank_is_preserved_for_genuine_string_settings(tmp_path: Path) -> None:
    """An unset API key really is the empty string, not a missing value."""
    env_file = tmp_path / ".env"
    env_file.write_text("GOOGLE_MAPS_API_KEY=\nSMTP_HOST=\n", encoding="utf-8")
    settings = Settings(_env_file=str(env_file))  # type: ignore[call-arg]
    assert settings.google_maps_api_key == ""
    assert settings.smtp_host == ""


def test_env_example_declares_no_hardcoded_exchange_rate() -> None:
    """Parity must not be configurable into existence."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8").upper()
    for forbidden in ("USD_EUR_RATE", "CASE_USD_EUR", "FX_RATE="):
        assert forbidden not in text


# ---------------------------------------------------------------------------
# Raster configuration
# ---------------------------------------------------------------------------


def test_source_raster_is_derived_from_zoom_and_scale() -> None:
    cfg = Settings().satellite_image_config
    assert cfg.source_width_px == cfg.requested_width_px * cfg.scale
    assert cfg.source_width_px == 1280
    assert cfg.ground_m_per_source_px == pytest.approx(0.06185, abs=1e-5)
    assert cfg.ground_span_m == pytest.approx(79.168, abs=0.01)


def test_ground_resolution_ignores_any_image_dimensions() -> None:
    """Scale is a function of the projection, not of a file on disk."""
    assert meters_per_source_pixel(-34.04658242871865, 20, 2) == pytest.approx(
        Settings().satellite_image_config.ground_m_per_source_px, rel=1e-12
    )


def test_invalid_map_size_is_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(google_maps_size="not-a-size")


@pytest.mark.parametrize(("size", "expected"), [(3.6, 9), (6.0, 15), (9.6, 24)])
def test_panel_counts_are_derived_from_the_panel_rating(size: float, expected: int) -> None:
    assert Settings().required_panel_count(size) == expected


def test_case_location_keeps_both_coordinates() -> None:
    location = Settings().case_location
    assert location.raw_case_latitude > 0, "the brief's value, kept verbatim"
    assert location.resolved_latitude < 0, "the value actually used"
    assert location.raw_case_latitude == pytest.approx(-location.resolved_latitude)
    assert location.source_verified is True
    assert "minus sign" in location.resolution_note
