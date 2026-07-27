"""Typed application configuration.

Every externally-controllable value lands here so that no service reaches for
`os.environ` directly. Two rules are enforced structurally rather than by
convention:

1. There is no USD/EUR parity setting. A hardcoded exchange rate is never
   permitted, so the option to configure one does not exist.
2. The raw case coordinate and the resolved case coordinate are separate
   fields. The brief prints a latitude that is open sea; we record what it
   said and what we actually use, and never silently overwrite one with the
   other. See docs/location-verification.md.
"""

from __future__ import annotations

import math
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_ROOT = Path(__file__).resolve().parents[2]


def _default_repo_root() -> Path:
    """Repo root when running from a checkout.

    In the container the package sits at ``/app/app`` and there is no
    equivalent ancestor, so this is only a default - ``FIXTURES_DIR`` is the
    supported way to point at the data explicitly.
    """
    resolved = Path(__file__).resolve()
    return resolved.parents[4] if len(resolved.parents) > 4 else API_ROOT


REPO_ROOT = _default_repo_root()

EARTH_RADIUS_M = 6_378_137.0
WEB_MERCATOR_TILE_SIZE_PX = 256


class MapsMode(StrEnum):
    LIVE = "live"
    FIXTURE = "fixture"


class PvgisMode(StrEnum):
    LIVE = "live"
    FIXTURE = "fixture"


class FxMode(StrEnum):
    LIVE = "live"
    FIXTURE = "fixture"


class LlmProvider(StrEnum):
    OLLAMA = "ollama"
    RULES = "rules"
    DISABLED = "disabled"


class EmailMode(StrEnum):
    CONSOLE = "console"
    SMTP = "smtp"


class CaseLocationSettings(BaseModel):
    """The coordinate as printed in the brief, and the one actually used."""

    raw_case_latitude: float
    raw_case_longitude: float

    resolved_latitude: float
    resolved_longitude: float

    resolution_note: str
    source_verified: bool


class SatelliteImageConfig(BaseModel):
    """Authoritative description of the source-map raster.

    Ground resolution is derived from Web Mercator plus the verified zoom and
    scale. It is never derived from the pixel dimensions of whatever image
    happens to be on screen: a fixture may have been cropped or resized, and
    its dimensions carry no reliable scale information.
    """

    center_latitude: float
    center_longitude: float

    zoom: int
    requested_width_px: int
    requested_height_px: int
    scale: int

    map_type: str = "satellite"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source_width_px(self) -> int:
        return self.requested_width_px * self.scale

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source_height_px(self) -> int:
        return self.requested_height_px * self.scale

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ground_m_per_source_px(self) -> float:
        """Metres of ground per pixel of the source raster."""
        return meters_per_source_pixel(self.center_latitude, self.zoom, self.scale)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ground_span_m(self) -> float:
        return self.source_width_px * self.ground_m_per_source_px


def meters_per_source_pixel(latitude_deg: float, zoom: int, scale: int) -> float:
    """Web Mercator ground resolution for one pixel of the source raster.

    metersPerLogicalPixel = cos(lat) * 2 * pi * R / (256 * 2**zoom)
    metersPerSourcePixel  = metersPerLogicalPixel / scale
    """
    if scale < 1:
        raise ValueError("scale must be >= 1")
    if zoom < 0 or zoom > 23:
        raise ValueError("zoom out of range")
    if not -90.0 <= latitude_deg <= 90.0:
        raise ValueError("latitude out of range")

    circumference = 2.0 * math.pi * EARTH_RADIUS_M
    logical = (
        math.cos(math.radians(latitude_deg))
        * circumference
        / (WEB_MERCATOR_TILE_SIZE_PX * (2**zoom))
    )
    return logical / scale


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", API_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    log_level: str = "INFO"

    web_base_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"

    database_url: str = "sqlite+aiosqlite:///./solarvis.db"

    # --- case location -----------------------------------------------------
    case_raw_latitude: float = 34.04658242871865
    case_raw_longitude: float = 18.46491476666948
    case_resolved_latitude: float = -34.04658242871865
    case_resolved_longitude: float = 18.46491476666948

    # --- maps --------------------------------------------------------------
    maps_mode: MapsMode = MapsMode.FIXTURE
    google_maps_api_key: str = ""
    google_maps_zoom: int = 20
    google_maps_size: str = "640x640"
    google_maps_scale: int = 2
    google_maps_maptype: str = "satellite"

    # --- pvgis -------------------------------------------------------------
    pvgis_mode: PvgisMode = PvgisMode.LIVE
    pvgis_fallback_enabled: bool = True
    pvgis_base_url: str = "https://re.jrc.ec.europa.eu/api/v5_3"
    pvgis_system_loss_percent: float = 14.0
    pvgis_technology: str = "crystSi"
    pvgis_mounting_place: str = "building"
    pvgis_timeout_seconds: float = 15.0
    pvgis_cache_ttl_hours: int = 168

    # --- fx ----------------------------------------------------------------
    fx_mode: FxMode = FxMode.LIVE
    fx_provider: str = "frankfurter"
    fx_data_provider: str = "ECB"
    fx_base_url: str = "https://api.frankfurter.dev/v2"
    fx_base_currency: str = "USD"
    fx_quote_currency: str = "EUR"
    fx_timeout_seconds: float = 5.0
    fx_cache_ttl_hours: int = 24
    fx_fallback_enabled: bool = True
    fx_max_cached_rate_age_days: int = 7

    # --- llm ---------------------------------------------------------------
    llm_provider: LlmProvider = LlmProvider.RULES
    llm_fallback_enabled: bool = True
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3.5:2b"
    ollama_timeout_seconds: float = 20.0

    # --- email -------------------------------------------------------------
    email_mode: EmailMode = EmailMode.CONSOLE
    smtp_host: str = ""
    smtp_port: int = 0
    smtp_username: str = ""
    smtp_password: str = ""
    salesperson_email: str = ""

    # --- case constants ----------------------------------------------------
    case_capex_amount: float = 10_000.0
    case_capex_currency: str = "USD"
    case_electricity_price: float = 0.25
    case_electricity_currency: str = "EUR"

    # --- panel / roof ------------------------------------------------------
    panel_width_m: float = 1.0
    panel_height_m: float = 2.0
    panel_power_wp: int = 400
    panel_gap_m: float = 0.02
    roof_edge_setback_m: float = 0.0
    roof_pitch_deg: float = 25.0

    allowed_system_sizes_kwp: tuple[float, ...] = (3.6, 6.0, 9.6)

    # Explicit rather than inferred from the source-file depth, because the
    # container layout has no repo root above the package.
    fixtures_dir_override: str = ""

    @model_validator(mode="before")
    @classmethod
    def _blank_means_unset(cls, data: Any) -> Any:
        """Treat an empty value in `.env` as "not configured".

        `.env.example` ships blank placeholders for optional settings —
        `SMTP_PORT=`, `GOOGLE_MAPS_API_KEY=` — because that is how a template
        communicates "fill this in if you need it". Copying it to `.env` is the
        first step of the quick start, so an empty numeric field must fall back
        to its default rather than fail validation and take the whole app down.

        Blanks are preserved for genuine string settings, where "" is a
        meaningful value (an unset API key really is the empty string).
        """
        if not isinstance(data, dict):
            return data

        fields = cls.model_fields
        cleaned: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, str) and value.strip() == "":
                field = fields.get(key) or fields.get(str(key).lower())
                if field is not None and field.annotation is not str:
                    continue  # drop it, so the declared default applies
            cleaned[key] = value
        return cleaned

    @field_validator("google_maps_size")
    @classmethod
    def _validate_size(cls, v: str) -> str:
        parts = v.lower().split("x")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            raise ValueError("GOOGLE_MAPS_SIZE must look like 640x640")
        return v

    @property
    def maps_size_wh(self) -> tuple[int, int]:
        w, h = self.google_maps_size.lower().split("x")
        return int(w), int(h)

    @property
    def case_location(self) -> CaseLocationSettings:
        return CaseLocationSettings(
            raw_case_latitude=self.case_raw_latitude,
            raw_case_longitude=self.case_raw_longitude,
            resolved_latitude=self.case_resolved_latitude,
            resolved_longitude=self.case_resolved_longitude,
            resolution_note=(
                "The case brief omits the minus sign on the latitude. "
                "+34.0466, 18.4649 is open Mediterranean sea and does not "
                "reverse-geocode. -34.0466, 18.4649 resolves to Galway Road, "
                "Cape Town, Western Cape, South Africa; PVGIS returns land at "
                "17 m elevation; and imagery on that bbox matches the brief's "
                "reference photographs. See docs/location-verification.md."
            ),
            source_verified=True,
        )

    @property
    def satellite_image_config(self) -> SatelliteImageConfig:
        w, h = self.maps_size_wh
        return SatelliteImageConfig(
            center_latitude=self.case_resolved_latitude,
            center_longitude=self.case_resolved_longitude,
            zoom=self.google_maps_zoom,
            requested_width_px=w,
            requested_height_px=h,
            scale=self.google_maps_scale,
            map_type=self.google_maps_maptype,
        )

    @property
    def repo_root(self) -> Path:
        return REPO_ROOT

    @property
    def fixtures_dir(self) -> Path:
        if self.fixtures_dir_override:
            return Path(self.fixtures_dir_override)
        return REPO_ROOT / "fixtures"

    @property
    def panel_power_kwp(self) -> float:
        return self.panel_power_wp / 1000.0

    def required_panel_count(self, system_size_kwp: float) -> int:
        """requestedPanelCount = requestedSystemSizeKwp * 1000 / panelWp."""
        exact = system_size_kwp * 1000.0 / self.panel_power_wp
        count = round(exact)
        if not math.isclose(exact, count, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(
                f"System size {system_size_kwp} kWp is not a whole number of "
                f"{self.panel_power_wp} Wp panels"
            )
        return int(count)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings_field = Field  # re-export to keep import surface small
