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
    return float(logical / scale)


#: Environments where a replay-backed proposal may be issued at all. Named here
#: rather than in the route or the health check, because the *settings* are what
#: refuse to construct outside them.
#: Operational margin on top of the worst-case external-call window.
#:
#: The probes and the FX call are not the only things between claiming a project
#: and releasing it - there is parsing, layout, the snapshot write and whatever
#: the host is doing at the time. Thirty seconds is generous rather than tight,
#: because the cost of an over-long lease is a delay and the cost of a short one
#: is two batches racing.
ANALYSIS_LEASE_MARGIN_SECONDS = 30.0

TEST_ENVIRONMENTS = frozenset({"test", "e2e", "verification"})


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
    #: Where the satellite raster is fetched from.
    #:
    #: There is no fixture mode. The application always makes a real HTTP request
    #: for imagery, exactly as it does for PVGIS, and the tests stay offline by
    #: pointing this at a local synthetic stub rather than by giving the
    #: application an offline path. A silently substituted raster is worse than a
    #: missing one: the overlay still renders, and it renders over the wrong roof.
    google_static_maps_base_url: str = "https://maps.googleapis.com/maps/api/staticmap"

    #: Which roof calibration profile to measure from.
    #:
    #: Empty means the committed Google profile. A test points this at a profile
    #: bound to the stub's synthetic raster, so the suite exercises the real
    #: verification path rather than a bypass flag - there is deliberately no
    #: "skip the check" setting, because that is the setting that gets left on.
    roof_calibration_path: str = ""
    google_maps_api_key: str = ""
    google_maps_zoom: int = 20
    google_maps_size: str = "640x640"
    google_maps_scale: int = 2
    google_maps_maptype: str = "satellite"

    # --- pvgis -------------------------------------------------------------
    #: There is no mode. PVGIS is always a real HTTP call to this URL.
    #:
    #: Tests point it at a local replay server, which is what keeps the suites
    #: offline without the *application* having an offline path. Only the
    #: canonical origin at the expected API version produces a `live`
    #: observation; anything else is `replay` and is not proposal-grade.
    pvgis_base_url: str = "https://re.jrc.ec.europa.eu/api/v5_3"
    pvgis_system_loss_percent: float = 14.0
    pvgis_technology: str = "crystSi"
    pvgis_mounting_place: str = "building"
    pvgis_timeout_seconds: float = 15.0

    #: How hard a single facet probe tries before the whole analysis fails.
    #:
    #: Two bounds, whichever binds first: a hard attempt count, and a wall-clock
    #: budget so a run of slow responses or a generous `Retry-After` cannot hold
    #: a customer's request open. The facets are probed concurrently, so the
    #: batch costs roughly one budget rather than four.
    pvgis_max_attempts: int = 4
    pvgis_retry_budget_seconds: float = 30.0
    pvgis_retry_base_delay_seconds: float = 0.5
    pvgis_retry_max_delay_seconds: float = 8.0

    #: Permit a proposal backed by a replayed capture rather than a live PVGIS
    #: observation.
    #:
    #: **False everywhere but the test harnesses**, and mechanically so: the
    #: validator below refuses to construct these settings if it is true outside
    #: a recognised test environment, so turning it on in a production profile
    #: fails start-up rather than quietly issuing replay-backed documents. It
    #: exists only because the stub-backed suites still have to exercise
    #: finalisation end to end.
    allow_replay_proposals: bool = False

    #: How long one analysis batch may hold its claim on a project.
    #:
    #: Validated below against the worst case it protects. A lease shorter than
    #: the work is worse than no lease: it hands the project to a second process
    #: while the first is still running and still intending to write.
    analysis_lease_seconds: float = 120.0

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

    #: How long finalisation will wait for the optional executive summary.
    #:
    #: Deliberately separate from, and shorter than, the transport timeout. The
    #: deterministic template is always ready and is what ships whenever the
    #: model fails any gate, so a customer's proposal must never be held open
    #: for two minutes waiting on prose that is an improvement rather than a
    #: requirement. Exceeded, the template is used and the source is reported
    #: as "deterministic" - the same outcome as any other failed gate.
    summary_timeout_seconds: float = 15.0

    # --- email -------------------------------------------------------------
    #: `console` records the rendered message and sends nothing; `smtp` sends.
    #:
    #: The two the case brief names, and no vendor SDK behind either. Console is
    #: never reported as delivery: the provider name travels on the delivery
    #: record, so the UI can say "recorded locally" rather than "sent".
    email_mode: EmailMode = EmailMode.CONSOLE
    smtp_host: str = ""
    smtp_port: int = 0
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True

    #: How long a single send may take, end to end.
    #:
    #: Short on purpose. The send happens inside a request, and a relay that
    #: hangs would otherwise hold a worker until the client gave up - leaving a
    #: delivery whose true outcome nobody can determine.
    smtp_timeout_seconds: float = 10.0

    #: The envelope sender. Required for `smtp`, and deliberately not defaulted
    #: to anything: a plausible-looking default address is how mail starts
    #: arriving from a domain nobody controls.
    email_from: str = ""
    email_from_name: str = "SolarVis"
    email_reply_to: str = ""

    #: Where the "your proposal was opened" notification goes. Empty disables
    #: it, which is the honest default - there is no operator account to infer
    #: an address from.
    salesperson_email: str = ""

    #: Master switch for outbound proposal email, independent of the provider.
    proposal_email_enabled: bool = True

    #: Whether the rendered PDF is attached to the proposal email.
    #:
    #: Off by default. Rendering runs Chromium inside the request, attachments
    #: hurt deliverability, and the artefact this product issues is a web page -
    #: the link is the deliverable. Opt in where those trade-offs are acceptable.
    proposal_email_attach_pdf: bool = False

    #: Salt for the stored view hash.
    #:
    #: Without one, `sha256(ip)` is a lookup table: the IPv4 space is small
    #: enough to enumerate in full, so an unsalted digest identifies the address
    #: exactly. Empty means unsalted, which is what the column held before.
    view_hash_salt: str = ""

    #: How long after a counted view an identical one is treated as the same
    #: visit. A customer scrolling back and forth is one person reading, not
    #: five separate openings.
    proposal_view_dedup_minutes: float = 30.0

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

    #: Angular step, in degrees, for the per-facet array-rotation search.
    #:
    #: `0` disables the search, and every array is laid parallel to its facet's
    #: eave - which is the only geometry a standard rail-and-clamp mounting
    #: system supports. A non-zero value lets each facet's array be turned to
    #: fit more panels; on the case roof that is +2 panels and requires bespoke
    #: sub-framing. See docs/known-limitations.md before enabling it in
    #: anything a customer will act on.
    panel_rotation_step_deg: float = 5.0
    #: Upper bound of the search. Beyond 90 deg a rectangle repeats itself, and
    #: the two orientations already cover the other half.
    panel_rotation_max_deg: float = 90.0

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

    @model_validator(mode="after")
    def _the_analysis_lease_outlasts_the_work_it_protects(self) -> Settings:
        """A lease shorter than the batch it guards is worse than none at all.

        It would expire while the holder is still probing, hand the project to
        a second batch, and leave the first about to write a snapshot it no
        longer owns. The fencing token catches that, but only after two batches
        have already done the same work - so the lease is sized to cover the
        worst case up front.
        """
        worst_case = self.pvgis_retry_budget_seconds + self.fx_timeout_seconds
        required = worst_case + ANALYSIS_LEASE_MARGIN_SECONDS
        if self.analysis_lease_seconds < required:
            raise ValueError(
                f"ANALYSIS_LEASE_SECONDS={self.analysis_lease_seconds:g} is shorter than the "
                f"work it protects: a PVGIS retry budget of {self.pvgis_retry_budget_seconds:g}s "
                f"plus an FX timeout of {self.fx_timeout_seconds:g}s plus "
                f"{ANALYSIS_LEASE_MARGIN_SECONDS:g}s of operational margin needs at least "
                f"{required:g}s."
            )
        return self

    @model_validator(mode="after")
    def _replay_proposals_are_confined_to_test_environments(self) -> Settings:
        """Refuse to start rather than quietly issue a replay-backed proposal.

        `ALLOW_REPLAY_PROPOSALS` exists so the stub-backed harnesses can still
        exercise finalisation. Left as a convention it would eventually be set
        somewhere it should not be, and the symptom - a proposal citing a
        replayed capture as a live observation - is exactly the thing this
        whole change exists to make impossible. So it is mechanical: outside a
        recognised test environment, enabling it is a start-up failure.
        """
        if self.allow_replay_proposals and self.app_env.lower() not in TEST_ENVIRONMENTS:
            raise ValueError(
                f"ALLOW_REPLAY_PROPOSALS is only permitted when APP_ENV is one of "
                f"{sorted(TEST_ENVIRONMENTS)}; APP_ENV is {self.app_env!r}. A proposal "
                f"must be backed by a live PVGIS observation."
            )
        return self

    @model_validator(mode="after")
    def _smtp_is_refused_in_a_test_environment(self) -> Settings:
        """Automated tests must not be *able* to send real email.

        Left as a convention - "don't configure SMTP in CI" - it holds until the
        day a `.env` is copied, a compose file is reused, or a developer runs
        the suite against their own configuration. The failure is not a red
        test: it is real mail arriving at a real customer from a test run, and
        nothing in the suite would notice.

        So it is mechanical, and it is the *settings* that refuse rather than
        the sender: in a recognised test environment, `EMAIL_MODE=smtp` is a
        start-up failure. Same shape, and the same reasoning, as
        `_replay_proposals_are_confined_to_test_environments` above.
        """
        if self.email_mode is EmailMode.SMTP and self.app_env.lower() in TEST_ENVIRONMENTS:
            raise ValueError(
                f"EMAIL_MODE=smtp is refused when APP_ENV is one of "
                f"{sorted(TEST_ENVIRONMENTS)}; APP_ENV is {self.app_env!r}. An automated "
                f"test must never be able to send real email."
            )
        return self

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
