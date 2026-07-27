"""Domain models.

These are pure data shapes. They carry no I/O and no business rules beyond
validation, so they can be imported by the geometry engine, the optimiser, the
services and the tests without dragging in a database or an HTTP client.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ProjectStep(StrEnum):
    LOCATION = "location"
    CONSUMPTION = "consumption"
    SYSTEM_SIZE = "system_size"
    ROOF_RECONSTRUCTION = "roof_reconstruction"
    PANEL_LAYOUT = "panel_layout"
    ENERGY_YIELD = "energy_yield"
    EXCHANGE_RATE = "exchange_rate"
    FINANCIAL_ANALYSIS = "financial_analysis"
    PROPOSAL = "proposal"
    COMPLETED = "completed"


STEP_ORDER: tuple[ProjectStep, ...] = (
    ProjectStep.LOCATION,
    ProjectStep.CONSUMPTION,
    ProjectStep.SYSTEM_SIZE,
    ProjectStep.ROOF_RECONSTRUCTION,
    ProjectStep.PANEL_LAYOUT,
    ProjectStep.ENERGY_YIELD,
    ProjectStep.EXCHANGE_RATE,
    ProjectStep.FINANCIAL_ANALYSIS,
    ProjectStep.PROPOSAL,
    ProjectStep.COMPLETED,
)


# --------------------------------------------------------------------------
# Geometry primitives
# --------------------------------------------------------------------------


class Point2D(BaseModel):
    x: float
    y: float

    def __iter__(self) -> Iterator[float]:  # type: ignore[override]
        yield self.x
        yield self.y

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)


class Point3D(BaseModel):
    x: float
    y: float
    z: float


class GeoPoint(BaseModel):
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)


# --------------------------------------------------------------------------
# Roof
# --------------------------------------------------------------------------


class RoofEdgeType(StrEnum):
    EAVE = "eave"
    HIP = "hip"
    RIDGE = "ridge"


class RoofVertex(BaseModel):
    id: str
    source_pixel: Point2D
    projected_metric: Point2D | None = None
    height_m: float | None = None


class RoofEdge(BaseModel):
    """An edge of the roof.

    `projected_length_m` is always available: it is the plan-view length.

    `true_3d_length_m` is only populated when both endpoint heights are known.
    It is deliberately NOT computed as `projected / cos(pitch)`: that identity
    holds only for an edge running along the line of maximum slope. A hip runs
    diagonally across the slope, so applying the pitch correction to it
    overstates its length. See docs/geometry.md (assumption A-GEO-1).
    """

    id: str
    start_vertex_id: str
    end_vertex_id: str

    edge_type: RoofEdgeType

    projected_length_m: float
    true_3d_length_m: float | None = None


class RoofFacet(BaseModel):
    id: str
    label: str

    vertex_ids: list[str]

    source_pixel_polygon: list[Point2D]
    projected_metric_polygon: list[Point2D]

    eave_edge_id: str

    pitch_deg: float
    compass_azimuth_deg: float
    pvgis_aspect_deg: float

    projected_area_m2: float
    sloped_area_m2: float

    specific_yield_kwh_per_kwp: float | None = None

    @field_validator("source_pixel_polygon", "projected_metric_polygon")
    @classmethod
    def _at_least_triangle(cls, v: list[Point2D]) -> list[Point2D]:
        if len(v) < 3:
            raise ValueError("a facet polygon needs at least 3 vertices")
        return v


class RoofModel(BaseModel):
    """The calibrated roof.

    Vertices are stored in SOURCE-MAP pixel coordinates - the canonical raster
    defined by the verified centre/zoom/size/scale - never in viewport or
    fixture-image coordinates.
    """

    id: str
    vertices: list[RoofVertex]
    edges: list[RoofEdge]
    facets: list[RoofFacet]

    pitch_deg: float
    ground_m_per_source_px: float

    source_width_px: int
    source_height_px: int

    total_projected_area_m2: float
    total_sloped_area_m2: float

    def vertex(self, vertex_id: str) -> RoofVertex:
        for v in self.vertices:
            if v.id == vertex_id:
                return v
        raise KeyError(f"unknown vertex {vertex_id!r}")

    def edge(self, edge_id: str) -> RoofEdge:
        for e in self.edges:
            if e.id == edge_id:
                return e
        raise KeyError(f"unknown edge {edge_id!r}")

    def facet(self, facet_id: str) -> RoofFacet:
        for f in self.facets:
            if f.id == facet_id:
                return f
        raise KeyError(f"unknown facet {facet_id!r}")


# --------------------------------------------------------------------------
# Panels
# --------------------------------------------------------------------------


class PanelOrientation(StrEnum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"


class SolarPanel(BaseModel):
    id: str
    facet_id: str

    orientation: PanelOrientation

    power_wp: int
    surface_width_m: float
    surface_height_m: float

    surface_polygon: list[Point2D]
    projected_metric_polygon: list[Point2D]
    source_pixel_polygon: list[Point2D]


class FacetLayout(BaseModel):
    facet_id: str
    orientation: PanelOrientation
    panels: list[SolarPanel]

    @property
    def panel_count(self) -> int:
        return len(self.panels)


class PanelLayout(BaseModel):
    requested_system_size_kwp: float
    requested_panel_count: int

    placed_panel_count: int
    feasible_system_size_kwp: float

    facet_layouts: list[FacetLayout]

    capacity_warning: str | None = None

    @property
    def panels(self) -> list[SolarPanel]:
        return [p for fl in self.facet_layouts for p in fl.panels]

    @property
    def is_fully_satisfied(self) -> bool:
        return self.placed_panel_count >= self.requested_panel_count


# --------------------------------------------------------------------------
# Energy
# --------------------------------------------------------------------------


class DataSource(StrEnum):
    LIVE = "live"
    CACHE = "cache"
    FIXTURE = "fixture"
    LIVE_FALLBACK_CACHE = "live_fallback_cache"
    LIVE_FALLBACK_FIXTURE = "live_fallback_fixture"


class FacetYieldResult(BaseModel):
    facet_id: str

    panel_count: int
    installed_power_kwp: float

    pitch_deg: float
    compass_azimuth_deg: float
    pvgis_aspect_deg: float

    annual_production_kwh: float
    specific_yield_kwh_per_kwp: float
    monthly_production_kwh: list[float]

    data_source: DataSource

    @field_validator("monthly_production_kwh")
    @classmethod
    def _twelve_months(cls, v: list[float]) -> list[float]:
        if len(v) != 12:
            raise ValueError("monthly_production_kwh must have exactly 12 entries")
        return v


class YieldResult(BaseModel):
    facet_results: list[FacetYieldResult]

    total_annual_production_kwh: float
    total_monthly_production_kwh: list[float]

    installed_power_kwp: float
    data_source: DataSource

    radiation_database: str | None = None


# --------------------------------------------------------------------------
# FX and finance
# --------------------------------------------------------------------------


class ExchangeRateSource(StrEnum):
    LIVE = "live"
    CACHE = "cache"
    FIXTURE = "fixture"
    LIVE_FALLBACK_CACHE = "live_fallback_cache"
    LIVE_FALLBACK_FIXTURE = "live_fallback_fixture"

    @property
    def is_live(self) -> bool:
        return self is ExchangeRateSource.LIVE

    @property
    def is_fixture(self) -> bool:
        return self in (
            ExchangeRateSource.FIXTURE,
            ExchangeRateSource.LIVE_FALLBACK_FIXTURE,
        )


class ExchangeRate(BaseModel):
    source_api: str
    data_provider: str

    rate_date: date

    base_currency: str
    quote_currency: str

    rate: Decimal

    retrieval_source: ExchangeRateSource
    retrieved_at: datetime

    @field_validator("rate")
    @classmethod
    def _positive_finite(cls, v: Decimal) -> Decimal:
        if not v.is_finite():
            raise ValueError("exchange rate must be finite")
        if v <= 0:
            raise ValueError("exchange rate must be positive")
        return v

    @field_validator("base_currency", "quote_currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class CapexConversion(BaseModel):
    original_amount: Decimal
    original_currency: str

    converted_amount: Decimal
    converted_currency: str

    exchange_rate: ExchangeRate


class CashFlowYear(BaseModel):
    year: int
    annual_savings_eur: Decimal
    cumulative_cash_flow_eur: Decimal


class FinancialResult(BaseModel):
    annual_consumption_kwh: float
    annual_production_kwh: float

    covered_energy_kwh: float
    coverage_percent: float

    electricity_price_eur_per_kwh: Decimal
    annual_savings_eur: Decimal

    capex_conversion: CapexConversion

    simple_payback_years: float | None
    twenty_year_net_benefit_eur: Decimal

    cash_flow: list[CashFlowYear]


# --------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------


class ChatIntent(StrEnum):
    PROVIDE_LOCATION = "provide_location"
    PROVIDE_CONSUMPTION = "provide_consumption"
    SELECT_SYSTEM_SIZE = "select_system_size"

    ASK_ROOF_QUESTION = "ask_roof_question"
    ASK_ENERGY_QUESTION = "ask_energy_question"
    ASK_FINANCIAL_QUESTION = "ask_financial_question"

    CONFIRM = "confirm"
    UNKNOWN = "unknown"


class ParsedChatMessage(BaseModel):
    intent: ChatIntent

    latitude: float | None = None
    longitude: float | None = None

    monthly_consumption_kwh: float | None = Field(default=None, gt=0, le=1_000_000)

    # PEP 586 does not admit float literals, so mypy flags this - but the
    # runtime behaviour is exactly what is wanted: Pydantic enforces the
    # whitelist, and the generated JSON schema constrains the model's output to
    # these three values. Widening it to `float` would lose both.
    system_size_kwp: Literal[3.6, 6.0, 9.6] | None = None  # type: ignore[valid-type]

    confidence: float = Field(ge=0, le=1)

    @field_validator("latitude")
    @classmethod
    def _lat_range(cls, v: float | None) -> float | None:
        if v is not None and not -90.0 <= v <= 90.0:
            raise ValueError("latitude out of range")
        return v

    @field_validator("longitude")
    @classmethod
    def _lon_range(cls, v: float | None) -> float | None:
        if v is not None and not -180.0 <= v <= 180.0:
            raise ValueError("longitude out of range")
        return v


# --------------------------------------------------------------------------
# Proposal
# --------------------------------------------------------------------------


class Proposal(BaseModel):
    id: UUID
    project_id: UUID

    share_token: str

    raw_location_input: str | None
    location: GeoPoint

    monthly_consumption_kwh: float
    annual_consumption_kwh: float

    electricity_price_eur_per_kwh: Decimal

    requested_system_size_kwp: float
    feasible_system_size_kwp: float

    requested_panel_count: int
    placed_panel_count: int

    roof: RoofModel
    layout: PanelLayout
    yield_result: YieldResult
    financial_result: FinancialResult

    ai_summary: str | None = None
    capacity_warning: str | None = None

    layout_snapshot_path: str | None = None

    created_at: datetime


def cos_pitch(pitch_deg: float) -> float:
    """cos of the roof pitch, guarded against a degenerate vertical roof."""
    c = math.cos(math.radians(pitch_deg))
    if c <= 1e-9:
        raise ValueError(f"pitch {pitch_deg} deg is degenerate (cos ~ 0)")
    return c
