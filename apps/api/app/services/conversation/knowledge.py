"""Curated help knowledge - the "how does this work?" answers.

A closed, typed registry of about two dozen entries. No vector store, no
retrieval, no embedding model: the question space this assistant serves is
small and known, and a lookup table over it is both faster and auditable in a
way a similarity search is not. When no entry matches, the honest answer is
that there is no entry - which a nearest-neighbour search can never say.

**No entry contains an engineering number.** Every figure is a `{placeholder}`
resolved from :class:`Settings` at render time, so the day the panel wattage or
the roof pitch changes, the prose changes with it instead of quietly going
stale. `tests/unit/test_conversation_knowledge.py` enforces this by rejecting
any digit in a body and by checking every number in a *rendered* entry against
the settings-derived values it was allowed to use.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.config import Settings, get_settings
from app.services.conversation.actions import AnswerState, Topic
from app.services.financial import ANALYSIS_YEARS

# ---------------------------------------------------------------------------
# Placeholders - the only route by which a number reaches a help answer
# ---------------------------------------------------------------------------


def _sizes(s: Settings) -> str:
    sizes = [f"{v:g}" for v in s.allowed_system_sizes_kwp]
    return f"{', '.join(sizes[:-1])} or {sizes[-1]} kWp"


def _size_table(s: Settings) -> str:
    return "; ".join(
        f"{size:g} kWp = {s.required_panel_count(size)} panels"
        for size in s.allowed_system_sizes_kwp
    )


def _panel_area(s: Settings) -> str:
    return f"{s.panel_width_m * s.panel_height_m:g} m2"


RESOLVERS: dict[str, Callable[[Settings], str]] = {
    "case_lat": lambda s: f"{s.case_resolved_latitude:.6f}",
    "case_lon": lambda s: f"{s.case_resolved_longitude:.6f}",
    "brief_lat": lambda s: f"{s.case_raw_latitude:.6f}",
    "pitch_deg": lambda s: f"{s.roof_pitch_deg:g} degrees",
    "panel_size": lambda s: f"{s.panel_width_m:g} m x {s.panel_height_m:g} m",
    "panel_area": _panel_area,
    "panel_power": lambda s: f"{s.panel_power_wp:g} Wp",
    "panel_power_kwp": lambda s: f"{s.panel_power_kwp:g} kWp",
    "panel_gap": lambda s: f"{s.panel_gap_m:g} m",
    "setback": lambda s: f"{s.roof_edge_setback_m:g} m",
    "sizes": _sizes,
    "size_table": _size_table,
    "electricity_price": lambda s: (
        f"{s.case_electricity_currency} {s.case_electricity_price:.2f} per kWh"
    ),
    "capex": lambda s: f"{s.case_capex_currency} {s.case_capex_amount:,.0f}",
    "fx_provider": lambda s: s.fx_data_provider,
    "fx_source_api": lambda s: s.fx_provider,
    "fx_pair": lambda s: f"{s.fx_base_currency}/{s.fx_quote_currency}",
    "fx_max_age": lambda s: f"{s.fx_max_cached_rate_age_days:g} days",
    "pvgis_loss": lambda s: f"{s.pvgis_system_loss_percent:g}%",
    "pvgis_technology": lambda s: s.pvgis_technology,
    "pvgis_mounting": lambda s: s.pvgis_mounting_place,
    "map_zoom": lambda s: f"{s.google_maps_zoom:g}",
    "map_scale": lambda s: f"{s.google_maps_scale:g}x",
    "ground_per_px": lambda s: f"{s.satellite_image_config.ground_m_per_source_px:.4f} m",
    "analysis_years": lambda _: f"{ANALYSIS_YEARS} years",
}

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HelpEntry:
    """One curated explanation, and the phrasings that select it."""

    key: str
    topic: Topic
    body: str
    #: Regexes matched against the *normalised* message, in registry order.
    triggers: tuple[str, ...] = ()
    #: Methodology by default: help knowledge explains how, not what the
    #: numbers came out as. Scope entries override this.
    state: AnswerState = AnswerState.ANSWERABLE_AS_METHODOLOGY
    _compiled: tuple[re.Pattern[str], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_compiled", tuple(re.compile(t) for t in self.triggers))

    @property
    def settings_keys(self) -> tuple[str, ...]:
        """The settings-derived placeholders this entry interpolates."""
        return tuple(dict.fromkeys(_PLACEHOLDER.findall(self.body)))

    def matches(self, text: str) -> bool:
        return any(p.search(text) for p in self._compiled)

    def render(self, settings: Settings | None = None) -> str:
        s = settings or get_settings()
        return _PLACEHOLDER.sub(lambda m: RESOLVERS[m.group(1)](s), self.body)


ENTRIES: tuple[HelpEntry, ...] = (
    HelpEntry(
        key="fixed_location",
        topic=Topic.LOCATION,
        triggers=(
            r"\bwhy\b.*\b(location|address|coordinate|place)\b",
            r"\b(different|another|other|my own|new)\b.*\b(location|address|property|house|roof)\b",
            r"\bcan (i|we|you)\b.*\b(use|change|enter|analyse|analyze)\b.*"
            r"\b(location|address|property)\b",
            r"\bfixed\b.*\b(location|property|case)\b",
        ),
        body=(
            "This build analyses one verified property: Galway Road, Cape Town, at "
            "{case_lat}, {case_lon}. The roof there is traced from a satellite raster "
            "and calibrated by hand, so every measurement downstream is anchored to a "
            "real building. Analysing a different address would need a fresh image "
            "fetch and a new calibration, and neither is automated here - so the "
            "location you type is recorded, and the analysis runs on the case property."
        ),
    ),
    HelpEntry(
        key="latitude_sign",
        topic=Topic.LOCATION,
        triggers=(
            r"\b(minus|negative|sign)\b.*\blatitude\b",
            r"\blatitude\b.*\b(minus|negative|sign|wrong|sea|ocean)\b",
        ),
        body=(
            "The brief prints the latitude as {brief_lat}, without the minus sign. "
            "That point is open sea and does not reverse-geocode. The southern "
            "reading, {case_lat}, resolves to Galway Road in Cape Town, returns land "
            "elevation from PVGIS, and matches the reference photographs - so that is "
            "the one used. Both values stay on record."
        ),
    ),
    HelpEntry(
        key="consumption_unit",
        topic=Topic.CONSUMPTION,
        triggers=(
            r"\b(what|which)\b.*\b(unit|units|format)\b",
            r"\b(annual|yearly|per year|a year)\b.*\b(instead|rather|consumption|figure)\b",
            r"\bcannot\b.*\bannual\b",
            r"\bwhy\b.*\b(monthly|consumption)\b",
            r"\bwhere\b.*\b(find|get|see)\b.*\b(consumption|usage|bill)\b",
        ),
        body=(
            "Give the figure your electricity bill shows for a typical month, in kWh. "
            "An annual figure works too - say so and it is divided by twelve. The "
            "number drives coverage and savings only; it never changes the roof, the "
            "layout or the modelled production."
        ),
    ),
    HelpEntry(
        key="kw_vs_kwh",
        topic=Topic.CONSUMPTION,
        triggers=(
            r"\bkw\b.*\bkwh\b",
            r"\bkwh\b.*\bkw\b",
            r"\bdifference between\b.*\bkw",
            r"\bwhat (is|are|does)\b.*\bkwh\b",
        ),
        body=(
            "kW is a rate and kWh is an amount. A kettle draws about two kW while it "
            "is on; leaving it on for an hour uses two kWh. Bills are in kWh because "
            "you pay for the amount, not the rate."
        ),
    ),
    HelpEntry(
        key="kwp_definition",
        topic=Topic.SYSTEM_SIZE,
        triggers=(
            r"\bwhat (is|does)\b.*\bkwp\b",
            r"\bkwp\b.*\b(mean|means|stand for)\b",
            r"\bpeak\b.*\bpower\b",
        ),
        body=(
            "kWp is peak power: what the array produces under standard test "
            "conditions - full reference sunlight, a fixed cell temperature. It is a "
            "label for the size of the array, not a promise about any given hour. "
            "Real output depends on the sun the roof actually receives, which is what "
            "the yield model estimates."
        ),
    ),
    HelpEntry(
        key="system_options",
        topic=Topic.SYSTEM_SIZE,
        triggers=(
            r"\b(what|which)\b.*\boptions?\b",
            r"\bwhat (sizes|systems)\b",
            r"\bhow many (sizes|options|choices)\b",
            r"\b(list|show)\b.*\b(sizes|options)\b",
        ),
        body=(
            "Three sizes are offered: {sizes}. At {panel_power} a panel that is "
            "{size_table}. A larger system produces more but is capped by what "
            "physically fits on the roof; if the panels do not fit, the feasible "
            "capacity is what every figure downstream is based on."
        ),
    ),
    HelpEntry(
        key="panel_power",
        topic=Topic.SYSTEM_SIZE,
        triggers=(
            r"\bhow many panels\b",
            r"\bpanel count\b",
            r"\bwhy\b.*\bpanels\b",
            r"\bpanel\b.*\b(wattage|power|rating|size|dimensions)\b",
        ),
        body=(
            "Each panel is rated {panel_power}, so the panel count is the system size "
            "divided by that: {size_table}. A panel measures {panel_size}, about "
            "{panel_area} of roof each, with {panel_gap} between neighbours."
        ),
    ),
    HelpEntry(
        key="roof_pitch",
        topic=Topic.ROOF,
        triggers=(
            r"\b(pitch|slope|tilt|angle)\b",
            r"\bhow steep\b",
        ),
        body=(
            "The roof is modelled at a uniform {pitch_deg} pitch, taken from the case "
            "brief. Pitch matters twice: it converts the projected area seen from "
            "above into true sloped area, and it is passed to the yield model as the "
            "array tilt."
        ),
    ),
    HelpEntry(
        key="pixel_to_metre",
        topic=Topic.ROOF,
        triggers=(
            r"\b(pixel|pixels|scale|metre|meter|measur)\w*\b.*\b(convert|ground|real|actual)\b",
            r"\bhow\b.*\b(measure|measured|know)\b.*\b(size|area|roof|dimensions)\b",
            r"\bhow big\b.*\b(pixel|image)\b",
            r"\bground resolution\b",
        ),
        body=(
            "The satellite image is a Web Mercator tile at zoom {map_zoom}, scale "
            "{map_scale}. That fixes the ground resolution at {ground_per_px} per "
            "source pixel at this latitude, computed from the projection rather than "
            "measured off the picture - a fixture that had been cropped or resized "
            "would otherwise carry a false scale. Every roof dimension is a pixel "
            "measurement multiplied by that constant."
        ),
    ),
    HelpEntry(
        key="roof_facets",
        topic=Topic.ROOF,
        triggers=(
            r"\bfacets?\b",
            r"\b(how many|what)\b.*\b(sides|planes|sections)\b.*\broof\b",
        ),
        body=(
            "A facet is one flat plane of the roof. Each is traced separately because "
            "each faces a different way, and orientation is the single biggest lever "
            "on how much a panel produces. Facets are ranked by modelled yield and "
            "filled in that order."
        ),
    ),
    HelpEntry(
        key="roof_edges",
        topic=Topic.ROOF,
        triggers=(r"\b(ridge|ridges|hip|hips|eave|eaves|valley|edge|edges)\b",),
        body=(
            "Edges are classified by the two planes that meet along them: an eave is "
            "the low, outer boundary; a ridge is the high line where two facets meet "
            "facing away from each other; a hip is a sloping join between adjacent "
            "facets. The classification is geometric, not cosmetic - it is what tells "
            "the layout which boundary is the bottom of a facet."
        ),
    ),
    HelpEntry(
        key="placement_method",
        topic=Topic.LAYOUT,
        triggers=(
            r"\bhow\b.*\b(place|placed|arrange|arranged|position)\b",
            r"\b(why|how)\b.*\blayout\b",
            r"\bportrait\b|\blandscape\b|\borientation\b",
        ),
        body=(
            "Panels are laid on a grid aligned to each facet's eave, tried in both "
            "portrait and landscape, and the orientation that fits more panels wins. "
            "A panel is kept only if its whole footprint lies inside the facet - no "
            "partial panels, no overhang. Facets are filled best-yield first, so the "
            "panels that do fit are on the best-performing planes."
        ),
    ),
    HelpEntry(
        key="setbacks",
        topic=Topic.LAYOUT,
        triggers=(
            r"\bsetback\b|\bmargin\b|\bclearance\b",
            r"\bgap\b.*\bpanels?\b",
            r"\bedge\b.*\b(space|spacing|distance)\b",
        ),
        body=(
            "Panels sit {panel_gap} apart and are inset {setback} from the facet "
            "edge. The setback is a configurable allowance for a fire or maintenance "
            "walkway; this case study uses the brief's value, which is why panels can "
            "reach the boundary of a facet here. A real installation would set it "
            "from the local code."
        ),
    ),
    HelpEntry(
        key="unplaced_panels",
        topic=Topic.LAYOUT,
        triggers=(
            r"\b(not|never|no|none|fewer|less)\b.*\bpanels?\b.*\b(fit|placed|there|side)\b",
            r"\bwhy\b.*\b(fewer|less|only)\b.*\bpanels?\b",
            r"\bcapacity warning\b",
        ),
        body=(
            "When the requested size needs more panels than physically fit, the "
            "shortfall is reported and the *feasible* capacity flows onward. Nothing "
            "downstream ever sees the requested figure as though it were installed, "
            "so production, savings and payback all describe the system that could "
            "actually be built."
        ),
    ),
    HelpEntry(
        key="pvgis",
        topic=Topic.YIELD,
        triggers=(
            r"\bpvgis\b",
            r"\bwhere\b.*\b(production|yield|energy)\b.*\b(from|come)\b",
            r"\bhow\b.*\b(production|yield|output|generation)\b.*"
            r"\b(calculated|computed|estimated|modelled|modeled)\b",
        ),
        body=(
            "Production comes from PVGIS, the European Commission's photovoltaic "
            "database. Each facet is submitted separately with its own tilt and "
            "azimuth and its own installed capacity, at {pvgis_technology} "
            "technology, {pvgis_mounting} mounting and {pvgis_loss} system losses. "
            "The facet results are summed; nothing is extrapolated from one facet to "
            "another."
        ),
    ),
    HelpEntry(
        key="specific_yield",
        topic=Topic.YIELD,
        triggers=(
            r"\bspecific yield\b",
            r"\bkwh per kwp\b",
            r"\bwhy\b.*\b(north|south|east|west)\b",
        ),
        body=(
            "Specific yield is annual kWh per kWp installed - the fair way to compare "
            "two facets, because it divides out how many panels each one holds. In "
            "the southern hemisphere a north-facing plane sees the most sun, so it "
            "ranks highest and is filled first."
        ),
    ),
    HelpEntry(
        key="shading_limitations",
        topic=Topic.YIELD,
        triggers=(
            r"\bshad(e|ing|ows?)\b",
            r"\b(tree|trees|chimney|neighbour|neighbor|building)\b",
        ),
        body=(
            "Shading from trees, chimneys or neighbouring buildings is not modelled. "
            "PVGIS applies its horizon data for the location, but nothing on or "
            "immediately around this roof is traced. A site with real obstructions "
            "would produce less than the figures here suggest, and a site survey is "
            "the only way to know by how much."
        ),
    ),
    HelpEntry(
        key="yield_uncertainty",
        topic=Topic.YIELD,
        triggers=(
            r"\b(accurate|accuracy|reliable|confiden|certain|guarantee)\w*\b",
            r"\bhow close\b",
            r"\breal(ly)?\b.*\bproduce\b",
        ),
        body=(
            "These are modelled estimates from long-run climate averages, not a "
            "forecast for a particular year. Weather varies, panels degrade slowly, "
            "and inverter and wiring losses are folded into a single {pvgis_loss} "
            "figure rather than modelled individually. Treat the output as a "
            "feasibility estimate, not a performance guarantee."
        ),
    ),
    HelpEntry(
        key="fx_source",
        topic=Topic.FX,
        triggers=(
            r"\b(exchange rate|fx|conversion rate)\b",
            r"\b(usd|dollar|eur|euro)\b.*\b(rate|convert|conversion)\b",
            r"\bwhere\b.*\brate\b.*\b(from|come)\b",
        ),
        body=(
            "The {fx_pair} rate is the {fx_provider} reference rate, retrieved "
            "through the {fx_source_api} API. The rate, its date, its provider and "
            "the moment it was read are all recorded with the analysis, so any figure "
            "in the proposal can be traced back to the observation it used."
        ),
    ),
    HelpEntry(
        key="fx_fallback",
        topic=Topic.FX,
        triggers=(
            r"\b(rate|fx)\b.*\b(unavailable|down|fails?|failed|offline|stale|old)\b",
            r"\bwhat (if|happens)\b.*\brate\b",
            r"\bcached\b.*\brate\b",
        ),
        body=(
            "If the rate service cannot be reached, a cached rate is used provided it "
            "is under {fx_max_age}; failing that, a recorded fixture rate is used. "
            "Whichever applies is labelled in the proposal - a fallback rate is never "
            "presented as a live one."
        ),
    ),
    HelpEntry(
        key="capex_conversion",
        topic=Topic.FINANCE,
        triggers=(
            r"\b(capex|cost|price|investment|upfront)\b",
            r"\bhow much\b.*\b(cost|pay|spend)\b",
        ),
        body=(
            "The installed cost is the case brief's {capex}, converted at the "
            "recorded {fx_pair} rate. It is a fixed case figure, not a quotation: it "
            "does not vary with the system size you pick, so the sizes differ in what "
            "they produce rather than in what they cost."
        ),
    ),
    HelpEntry(
        key="annual_savings",
        topic=Topic.FINANCE,
        triggers=(
            r"\bsavings?\b",
            r"\bhow much\b.*\bsave\b",
            r"\bcoverage\b|\bcovered\b",
        ),
        body=(
            "Savings are the energy you no longer buy: production and consumption are "
            "compared, and only the smaller of the two counts, valued at "
            "{electricity_price}. Producing more than the household uses adds nothing "
            "to the saving, because this model assumes no export tariff."
        ),
    ),
    HelpEntry(
        key="payback",
        topic=Topic.FINANCE,
        triggers=(
            r"\bpayback\b|\bpay ?back\b|\bbreak ?even\b",
            r"\bhow long\b.*\b(pay|recover|return)\b",
            r"\broi\b|\breturn on investment\b",
        ),
        body=(
            "Simple payback is the converted capital cost divided by the annual "
            "saving. It is deliberately simple: no discount rate, no electricity "
            "price inflation, no maintenance, no degradation. That makes it easy to "
            "check by hand, and it is why the number should be read as an indicator "
            "rather than a financial projection."
        ),
    ),
    HelpEntry(
        key="cash_flow",
        topic=Topic.FINANCE,
        triggers=(
            r"\bcash ?flow\b",
            r"\b(twenty|net benefit|cumulative)\b",
            r"\bhow many years\b",
        ),
        body=(
            "The cash-flow table runs {analysis_years}, starting at the negative "
            "capital cost and adding the same annual saving each year. The year the "
            "cumulative line crosses zero is the payback year, and the closing figure "
            "is the net benefit over the period."
        ),
    ),
    HelpEntry(
        key="proposal_snapshot",
        topic=Topic.PROPOSAL,
        triggers=(
            r"\bproposal\b.*\b(change|update|edit|revise|fix)\b",
            r"\b(snapshot|frozen|immutable|locked)\b",
            r"\bwhat (is|happens)\b.*\bproposal\b",
        ),
        body=(
            "Finalising copies every computed figure into the proposal and freezes "
            "it. Nothing is recalculated when the proposal is opened later, which is "
            "what makes it reproducible: the document says what it said the day it "
            "was issued. Changing an input afterwards starts a revision and leaves "
            "the issued proposal exactly as it is."
        ),
    ),
    HelpEntry(
        key="share_link",
        topic=Topic.PROPOSAL,
        triggers=(
            r"\b(share|link|url|token|send)\b",
            r"\bwho can (see|view|read)\b",
        ),
        body=(
            "Finalising mints a share link carrying a random token. Anyone with the "
            "link can read the proposal, so treat it as a secret; there is no "
            "account, no password and no expiry. A revision finalises to its own new "
            "link, and the original keeps working unchanged."
        ),
    ),
    HelpEntry(
        key="pdf",
        topic=Topic.PROPOSAL,
        triggers=(r"\bpdf\b|\bdownload\b|\bprint\b",),
        body=(
            "The PDF is rendered from the same frozen snapshot as the share page, so "
            "the two cannot disagree. It is generated on request rather than stored."
        ),
    ),
    HelpEntry(
        key="privacy",
        topic=Topic.PRIVACY,
        triggers=(
            r"\b(privacy|private|gdpr|data protection)\b",
            r"\b(store|stored|storing|keep|save|saved)\b.*\b(data|information|details)\b",
            r"\bwhy do you need\b",
            r"\bwho (sees|has access)\b",
        ),
        body=(
            "What is kept is what you typed, the values derived from it, and the "
            "analysis output - held in this application's own database. Two outside "
            "services are called during an analysis: PVGIS, with the case "
            "coordinates and array geometry, and the exchange-rate API, which "
            "receives no project data at all. Nothing is sold, shared or used for "
            "advertising, and the location you type is recorded for the transcript "
            "rather than sent anywhere."
        ),
    ),
    HelpEntry(
        key="scope_limitations",
        topic=Topic.GENERAL,
        triggers=(
            r"\bwhat (can|do) you (do|help)\b",
            r"\b(scope|limitations?|cannot|can not)\b",
            r"\bare you\b.*\b(human|bot|ai|real)\b",
            r"\bwho are you\b|\bwhat are you\b",
        ),
        body=(
            "This is a state-aware assistant for one solar feasibility workflow: it "
            "takes a location, a monthly consumption and a system size, then "
            "reconstructs the roof, places panels, models production, converts the "
            "cost and builds a proposal. It answers questions about those steps and "
            "the assumptions behind them. It is not an electrical designer: no "
            "structural certification, no permitting, no wiring or inverter design, "
            "no binding quotation, and no reconstruction of roofs other than the case "
            "property."
        ),
        state=AnswerState.OUT_OF_SCOPE,
    ),
    HelpEntry(
        key="workflow_overview",
        topic=Topic.GENERAL,
        triggers=(
            r"\bwhat (is|are) (the )?(next|step|steps)\b",
            r"\bwhat happens (next|now|after)\b",
            r"\bhow (long|many steps)\b",
            r"\bwhere are we\b",
        ),
        body=(
            "The order is fixed because each step needs the one before it: location, "
            "then monthly consumption, then system size; then the roof is "
            "reconstructed, panels are placed, production is modelled per facet, the "
            "capital cost is converted, the financials are calculated, and the "
            "proposal is assembled. You can ask a question at any point without "
            "losing your place."
        ),
    ),
    HelpEntry(
        key="change_a_value",
        topic=Topic.GENERAL,
        triggers=(
            r"\b(change|correct|update|edit|redo|fix)\b.*\b(answer|value|number|input|it)\b",
            r"\bcan i (change|go back|correct)\b",
            r"\bstart (over|again)\b",
            r"\breset\b",
        ),
        body=(
            'Say what you want it to be - "actually make it nine hundred kWh" - and '
            "the value is replaced. Only what genuinely depends on it is "
            "recalculated: changing consumption leaves the roof, the layout, the "
            "modelled production and the exchange rate exactly as they were. Starting "
            "over from scratch clears everything and asks for confirmation first."
        ),
    ),
)

BY_KEY: dict[str, HelpEntry] = {entry.key: entry for entry in ENTRIES}

#: Used when a topic is recognised but no trigger matched - better a relevant
#: explanation than a shrug.
DEFAULT_FOR_TOPIC: dict[Topic, str] = {
    Topic.LOCATION: "fixed_location",
    Topic.CONSUMPTION: "consumption_unit",
    Topic.SYSTEM_SIZE: "system_options",
    Topic.ROOF: "roof_facets",
    Topic.LAYOUT: "placement_method",
    Topic.YIELD: "pvgis",
    Topic.FX: "fx_source",
    Topic.FINANCE: "payback",
    Topic.PROPOSAL: "proposal_snapshot",
    Topic.PRIVACY: "privacy",
    Topic.GENERAL: "scope_limitations",
}


def find_entry(
    text: str, topic: Topic | None = None, *, allow_topic_default: bool = True
) -> HelpEntry | None:
    """The best curated entry for a message, or None.

    A trigger match on the classified topic wins; a trigger match elsewhere is
    next, because the topic classifier is coarse and a message that names
    "payback" is about payback whatever the classifier decided.

    `allow_topic_default` is the important argument, and it exists because of a
    real defect. The topic default used to apply unconditionally, and the answer
    service always supplied a topic - defaulted from the *current step* when the
    message itself named none. So any question the triggers did not recognise
    came back as the current step's default help entry. At the consumption step,
    "who built this?", "can you summarise the project?", "tell me about the
    panels" and "what have I told you so far?" all returned the same paragraph
    about reading a figure off an electricity bill, word for word. That is the
    repetition customers noticed: not a fallback fired too often, but a fallback
    standing in for four different questions.

    So a topic inferred *from the message* may still supply a default, and a
    topic merely inherited from the workflow step may not.
    """
    matched = [entry for entry in ENTRIES if entry.matches(text)]

    if topic is not None:
        for entry in matched:
            if entry.topic is topic:
                return entry
    if matched:
        return matched[0]
    if topic is not None and allow_topic_default:
        return BY_KEY.get(DEFAULT_FOR_TOPIC.get(topic, ""))
    return None


__all__ = [
    "BY_KEY",
    "DEFAULT_FOR_TOPIC",
    "ENTRIES",
    "RESOLVERS",
    "HelpEntry",
    "find_entry",
]
