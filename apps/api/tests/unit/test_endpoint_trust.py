"""Which endpoints may back a proposal, decided in exactly one place.

A hostname is not enough. Anyone can resolve a name, plain HTTP offers no
origin guarantee at all, and a redirect moves a request off the origin whose
trustworthiness was just established. So trust is the whole tuple - scheme,
origin, and the API path compared **segment by segment**, because
`startswith("/api/v5_3")` cheerfully accepts `/api/v5_31`.

`classify_endpoint` is the single implementation. Provenance, probe reuse,
finalisation, `/health/ready` and the sample-output guard all call it, because
five separate answers to "is this really PVGIS?" would drift and the one that
drifted would be the one that mattered.
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.domain.models import DataSource
from app.integrations.pvgis import TRUSTED_PVGIS_ORIGIN, classify_endpoint

# ---------------------------------------------------------------------------
# Trusted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://re.jrc.ec.europa.eu/api/v5_3",
        "https://re.jrc.ec.europa.eu/api/v5_3/",
        "https://RE.JRC.EC.EUROPA.EU/api/v5_3",  # host comparison is case-folded
        "https://re.jrc.ec.europa.eu:443/api/v5_3",  # the default port is not a difference
    ],
)
def test_the_canonical_endpoint_is_trusted(url) -> None:
    trust = classify_endpoint(url)
    assert trust.source is DataSource.LIVE
    assert trust.is_trusted
    assert trust.origin == TRUSTED_PVGIS_ORIGIN
    assert trust.api_version == "v5_3"
    assert trust.reason is None


# ---------------------------------------------------------------------------
# Not trusted, and why
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://re.jrc.ec.europa.eu/api/v5_3", "not HTTPS"),
        ("https://re.jrc.ec.europa.eu:8443/api/v5_3", "canonical origin"),
        ("https://evil.example/api/v5_3", "canonical origin"),
        ("https://re.jrc.ec.europa.eu.evil.example/api/v5_3", "canonical origin"),
        ("https://user:pw@re.jrc.ec.europa.eu/api/v5_3", "userinfo"),
        ("http://127.0.0.1:8102/api/v5_3", "not HTTPS"),
        ("not a url at all", "no host"),
        ("https://re.jrc.ec.europa.eu", "expected API path"),
    ],
)
def test_anything_else_is_replay_with_a_stated_reason(url, reason) -> None:
    trust = classify_endpoint(url)
    assert trust.source is DataSource.REPLAY
    assert not trust.is_trusted
    assert trust.reason is not None and reason in trust.reason


@pytest.mark.parametrize(
    "path",
    ["/api/v5_31", "/api/v5_3x", "/api/v5_2", "/api", "/apiv5_3", "/api/v5_3../evil"],
)
def test_the_api_path_is_matched_by_segment_not_prefix(path) -> None:
    """`startswith` would accept most of these. Segment equality does not.

    The near-misses are the point: `/api/v5_31` is a different API version and
    `/api/v5_3../evil` is a traversal wearing the right prefix.
    """
    trust = classify_endpoint(f"https://re.jrc.ec.europa.eu{path}")
    assert trust.source is DataSource.REPLAY, f"{path} was accepted"


def test_a_dot_segment_is_refused() -> None:
    trust = classify_endpoint("https://re.jrc.ec.europa.eu/api/v5_3/../v5_2")
    assert trust.source is DataSource.REPLAY
    assert trust.reason is not None and "normalised" in trust.reason


def test_the_default_configuration_is_trusted() -> None:
    """A clean clone points at the real service."""
    default = get_settings().model_copy(
        update={"pvgis_base_url": "https://re.jrc.ec.europa.eu/api/v5_3"}
    )
    assert classify_endpoint(default.pvgis_base_url).is_trusted


# ---------------------------------------------------------------------------
# /health/ready validates configuration, without calling out
# ---------------------------------------------------------------------------


def _status(**overrides):
    """A health report for a hypothetical configuration.

    `allow_replay_proposals` defaults to False here because the *session*
    settings have it on - this suite runs against a stub - and a production
    profile would not. Leaving it on would make every "production" case report
    unready for a reason the case is not about.
    """
    from app.api.v1.health import _pvgis_status

    base = {"allow_replay_proposals": False}
    return _pvgis_status(get_settings().model_copy(update={**base, **overrides}))


def test_health_reports_the_endpoint_that_will_be_called() -> None:
    status = _status(pvgis_base_url="https://re.jrc.ec.europa.eu/api/v5_3", app_env="production")

    assert status["endpoint"].endswith("/PVcalc")
    assert status["trusted"] is True
    assert status["ready"] is True
    assert status["detail"] is None


def test_an_untrusted_endpoint_is_not_ready_in_production() -> None:
    status = _status(pvgis_base_url="http://127.0.0.1:8102/api/v5_3", app_env="production")

    assert status["trusted"] is False
    assert status["ready"] is False
    assert "not proposal-grade" in status["detail"]


def test_an_untrusted_endpoint_is_fine_in_a_test_environment() -> None:
    """A local stub is expected there, and readiness should not cry wolf."""
    status = _status(pvgis_base_url="http://127.0.0.1:8102/api/v5_3", app_env="test")

    assert status["trusted"] is False
    assert status["ready"] is True


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"pvgis_max_attempts": 0}, "MAX_ATTEMPTS"),
        ({"pvgis_retry_budget_seconds": 0.0}, "RETRY_BUDGET"),
        ({"pvgis_timeout_seconds": 0.0}, "TIMEOUT"),
    ],
)
def test_nonsensical_retry_settings_are_reported(override, expected) -> None:
    status = _status(
        pvgis_base_url="https://re.jrc.ec.europa.eu/api/v5_3", app_env="production", **override
    )

    assert status["ready"] is False
    assert expected in status["detail"]


def test_readiness_makes_no_outbound_request(stub_requests) -> None:
    """A readiness probe that called PVGIS would be a way to get rate-limited."""
    _status(pvgis_base_url="https://re.jrc.ec.europa.eu/api/v5_3")
    assert stub_requests == []


# ---------------------------------------------------------------------------
# The replay override is confined to test environments, mechanically
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("app_env", ["test", "e2e", "verification"])
def test_replay_proposals_may_be_enabled_in_a_test_environment(app_env) -> None:
    from app.core.config import Settings

    settings = Settings(app_env=app_env, allow_replay_proposals=True)
    assert settings.allow_replay_proposals is True


@pytest.mark.parametrize("app_env", ["production", "development", "staging", ""])
def test_enabling_replay_proposals_anywhere_else_fails_startup(app_env) -> None:
    """Not a convention. The settings refuse to construct, so the process dies.

    Left as a convention this would eventually be set somewhere it should not
    be, and the symptom - a proposal citing a replayed capture as a live
    observation - is precisely what this change exists to make impossible.
    """
    import pydantic

    from app.core.config import Settings

    with pytest.raises(pydantic.ValidationError, match="ALLOW_REPLAY_PROPOSALS"):
        Settings(app_env=app_env, allow_replay_proposals=True)


def test_the_default_is_false_so_a_normal_profile_is_unaffected() -> None:
    """Asserted on the field default, not on a constructed instance.

    Constructing one here would read this suite's own environment, which turns
    the override on - so the assertion would be about the test harness rather
    than about what a clean deployment gets.
    """
    from app.core.config import Settings

    assert Settings.model_fields["allow_replay_proposals"].default is False


def test_health_reports_replay_enabled_outside_a_test_environment() -> None:
    """Defence in depth: start-up already refuses this configuration.

    Reaching it means something bypassed the settings, and this is the one
    signal an operator sees without reading logs.
    """
    from app.api.v1.health import _pvgis_status

    settings = get_settings().model_copy(
        update={
            "pvgis_base_url": "https://re.jrc.ec.europa.eu/api/v5_3",
            "app_env": "production",
            "allow_replay_proposals": True,
        }
    )
    status = _pvgis_status(settings)

    assert status["ready"] is False
    assert "ALLOW_REPLAY_PROPOSALS" in status["detail"]
