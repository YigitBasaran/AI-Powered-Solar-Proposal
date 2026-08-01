"""A local PVGIS replay server, so the application can always make a real call.

The application has no fixture mode. It speaks HTTP to whatever `PVGIS_BASE_URL`
names, and that is the only seam the tests use: pytest, the Playwright launchers
and both verification scripts all point it here. One mechanism, three harnesses,
no second mocking path to drift out of step with the first.

Three things make this more than a file server.

**It replays exactly, or refuses.** The four committed captures are indexed by
the aspect PVGIS itself rounded them to. An unknown aspect is a 400 naming what
was asked for. That is the deliberate opposite of the old `_load_fixture`, which
picked the *nearest* capture and so could quietly serve one facet's numbers for
another - the silent-approximation habit this whole change exists to remove.

**It checks the request, not just the aspect.** Pitch, loss, technology,
mounting and the coordinate are all validated, and `optimalangles` is refused
outright. So a request-shape regression - compass azimuth where PVGIS aspect
belongs, say - fails the entire suite rather than one unit test that happens to
assert the query string.

**Faults live in the URL path, never in server state.** `PVGIS_BASE_URL` is
per-process configuration, so a fault becomes a property of the *stack* under
test. Two workers can hit the healthy path and a failing one at the same instant
on the same process and each gets its own behaviour. There is deliberately no
"arm a failure" endpoint: that would be shared mutable state, which is the one
thing that could make these tests interfere with each other.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import threading
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CAPTURES_DIR = REPO_ROOT / "fixtures" / "pvgis"

#: The case coordinate every request must carry. A probe for anywhere else is a
#: bug in the caller, not something to answer politely.
CASE_LAT = -34.04658242871865
CASE_LON = 18.46491476666948
COORDINATE_TOLERANCE_DEG = 1e-6

EXPECTED_ANGLE_DEG = 25.0
EXPECTED_LOSS_PERCENT = 14.0
EXPECTED_TECHNOLOGY = "crystSi"
EXPECTED_MOUNTING = "building"

#: Query parameters that would let PVGIS choose the geometry for us. The whole
#: point is that the roof chooses it, so their presence is a contract violation.
FORBIDDEN_PARAMS = ("optimalangles", "optimalinclination", "optimalanglesfixed")


class StubContractError(Exception):
    """The request did not match what the application is supposed to send."""


# ---------------------------------------------------------------------------
# Captures
# ---------------------------------------------------------------------------


def load_captures(directory: Path) -> dict[int, dict[str, Any]]:
    """The committed PVcalc payloads, indexed by their own rounded azimuth.

    PVGIS echoes the azimuth it used as an integer, so `-169.38` comes back as
    `-169`. Indexing on `round()` of the requested aspect matches that exactly
    and needs no invention.
    """
    captures: dict[int, dict[str, Any]] = {}
    for path in sorted(directory.glob("pvcalc*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        try:
            azimuth = payload["inputs"]["mounting_system"]["fixed"]["azimuth"]["value"]
        except (KeyError, TypeError):  # pragma: no cover - a malformed capture
            continue
        captures[round(float(azimuth))] = payload
    return captures


def _scale(payload: dict[str, Any], peak_power_kwp: float) -> dict[str, Any]:
    """Scale energy by installed power, exactly as PVGIS does.

    Energy is linear in peak power; irradiation (`H(i)_*`), year-to-year spread
    (`SD_*`) and the loss percentages are not, because they are per square metre
    or already relative. Scaling those too would invent numbers, which is what
    the replay is here to avoid.
    """
    scaled = json.loads(json.dumps(payload))  # deep copy; payloads are small
    base_peak = float(scaled["inputs"]["pv_module"]["peak_power"])
    factor = peak_power_kwp / base_peak

    totals = scaled["outputs"]["totals"]["fixed"]
    for key in ("E_d", "E_m", "E_y"):
        if key in totals:
            totals[key] = round(float(totals[key]) * factor, 2)

    for row in scaled["outputs"]["monthly"]["fixed"]:
        for key in ("E_d", "E_m"):
            if key in row:
                row[key] = round(float(row[key]) * factor, 2)

    scaled["inputs"]["pv_module"]["peak_power"] = peak_power_kwp
    return scaled


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


def _number(params: Any, name: str) -> float:
    raw = params.get(name)
    if raw is None:
        raise StubContractError(f"missing required parameter {name!r}")
    try:
        return float(raw)
    except ValueError as exc:
        raise StubContractError(f"{name}={raw!r} is not a number") from exc


def validate_request(params: Any) -> tuple[float, float]:
    """Check the whole contract, and return `(aspect, peak_power_kwp)`.

    Raises `StubContractError`, which the caller turns into a 400 carrying the
    reason - so a failure names what was wrong rather than looking like PVGIS
    having a bad day.
    """
    for forbidden in FORBIDDEN_PARAMS:
        if forbidden in params:
            raise StubContractError(
                f"{forbidden!r} was sent; the roof geometry decides tilt and azimuth, never PVGIS"
            )

    lat = _number(params, "lat")
    lon = _number(params, "lon")
    if abs(lat - CASE_LAT) > COORDINATE_TOLERANCE_DEG:
        raise StubContractError(f"lat={lat} is not the case latitude {CASE_LAT}")
    if abs(lon - CASE_LON) > COORDINATE_TOLERANCE_DEG:
        raise StubContractError(f"lon={lon} is not the case longitude {CASE_LON}")

    angle = _number(params, "angle")
    if abs(angle - EXPECTED_ANGLE_DEG) > 0.5:
        raise StubContractError(f"angle={angle} is not the case pitch {EXPECTED_ANGLE_DEG}")

    loss = _number(params, "loss")
    if abs(loss - EXPECTED_LOSS_PERCENT) > 0.01:
        raise StubContractError(f"loss={loss} is not the configured {EXPECTED_LOSS_PERCENT}")

    technology = params.get("pvtechchoice")
    if technology != EXPECTED_TECHNOLOGY:
        raise StubContractError(f"pvtechchoice={technology!r}, expected {EXPECTED_TECHNOLOGY!r}")

    mounting = params.get("mountingplace")
    if mounting != EXPECTED_MOUNTING:
        raise StubContractError(f"mountingplace={mounting!r}, expected {EXPECTED_MOUNTING!r}")

    if params.get("outputformat") != "json":
        raise StubContractError(f"outputformat={params.get('outputformat')!r}, expected 'json'")

    return _number(params, "aspect"), _number(params, "peakpower")


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------


class PvgisStub:
    """Replay, contract check and fault injection for PVGIS `PVcalc`."""

    def __init__(self, captures_dir: Path | None = None) -> None:
        self.captures = load_captures(captures_dir or DEFAULT_CAPTURES_DIR)
        if not self.captures:  # pragma: no cover - only if the fixtures are gone
            raise RuntimeError(
                f"no PVGIS captures under {captures_dir or DEFAULT_CAPTURES_DIR}; "
                f"run scripts/fetch_pvgis_fixtures.py"
            )
        self.requests: list[dict[str, Any]] = []
        #: Attempt counters per (fault, aspect), so `flaky3` and `retry-after`
        #: can succeed on a later try. Keyed by aspect so concurrent facets do
        #: not consume each other's attempts.
        self._attempts: dict[tuple[str, int], int] = {}
        self._lock = threading.Lock()

    # -- helpers ---------------------------------------------------------

    def _next_attempt(self, fault: str, aspect: int) -> int:
        with self._lock:
            key = (fault, aspect)
            self._attempts[key] = self._attempts.get(key, 0) + 1
            return self._attempts[key]

    def _record(self, request: Request, fault: str, params: Any) -> None:
        with self._lock:
            self.requests.append(
                {
                    "fault": fault,
                    "path": request.url.path,
                    "aspect": params.get("aspect"),
                    "angle": params.get("angle"),
                    "peakpower": params.get("peakpower"),
                    "lat": params.get("lat"),
                    "lon": params.get("lon"),
                    "loss": params.get("loss"),
                    "pvtechchoice": params.get("pvtechchoice"),
                    "mountingplace": params.get("mountingplace"),
                    "at": datetime.now(UTC).isoformat(),
                }
            )

    def _payload_for(self, aspect: float, peak_power_kwp: float) -> dict[str, Any]:
        key = round(aspect)
        capture = self.captures.get(key)
        if capture is None:
            raise StubContractError(
                f"no committed capture for aspect {aspect} (rounded to {key}); "
                f"have {sorted(self.captures)}"
            )
        return _scale(capture, peak_power_kwp)

    # -- routes ----------------------------------------------------------

    async def pvcalc(self, request: Request) -> Response:
        fault = request.path_params.get("fault") or ""
        fault_arg = request.path_params.get("arg")
        params = request.query_params
        self._record(request, fault, params)

        try:
            aspect, peak = validate_request(params)
        except StubContractError as exc:
            return JSONResponse({"stubError": str(exc)}, status_code=400)

        rounded = round(aspect)

        # Faults that do not depend on the aspect.
        if fault == "unavailable":
            return JSONResponse({"stubFault": "unavailable"}, status_code=503)
        if fault == "permanent":
            return JSONResponse({"stubFault": "permanent"}, status_code=400)
        if fault == "server-error":
            return JSONResponse({"stubFault": "server-error"}, status_code=500)
        if fault == "redirect":
            return RedirectResponse("https://evil.example/api/v5_3/PVcalc", status_code=302)
        if fault == "garbage":
            return PlainTextResponse("<html>not json at all</html>", status_code=200)
        if fault == "timeout":
            # Longer than any timeout the application will use, but bounded so a
            # stray request cannot wedge the server thread forever.
            time.sleep(120)
            return JSONResponse({"stubFault": "timeout"}, status_code=200)
        if fault == "malformed":
            return JSONResponse(
                {"inputs": {}, "outputs": {"totals": {"fixed": {}}, "monthly": {"fixed": []}}},
                status_code=200,
            )
        if fault == "flaky3" and self._next_attempt(fault, rounded) <= 2:
            return JSONResponse({"stubFault": "flaky3"}, status_code=503)
        if fault == "retry-after" and self._next_attempt(fault, rounded) <= 1:
            return JSONResponse(
                {"stubFault": "retry-after"},
                status_code=429,
                headers={"Retry-After": "2"},
            )

        # Faults scoped to one aspect, so a batch can partially fail.
        if fault in {"one-facet", "mixed-raddb"} and fault_arg is not None:
            try:
                target = round(float(fault_arg))
            except ValueError:
                return JSONResponse({"stubError": f"bad fault aspect {fault_arg!r}"}, 400)
            if target == rounded:
                if fault == "one-facet":
                    return JSONResponse({"stubFault": "one-facet"}, status_code=503)
                payload = self._payload_for(aspect, peak)
                payload["inputs"]["meteo_data"]["radiation_db"] = "PVGIS-ERA5"
                return JSONResponse(payload)

        try:
            return JSONResponse(self._payload_for(aspect, peak))
        except StubContractError as exc:
            return JSONResponse({"stubError": str(exc)}, status_code=400)

    async def health(self, request: Request) -> Response:
        return JSONResponse({"ok": True, "captures": sorted(self.captures)})

    async def request_log(self, request: Request) -> Response:
        with self._lock:
            return JSONResponse(list(self.requests))

    async def reset(self, request: Request) -> Response:
        with self._lock:
            self.requests.clear()
            self._attempts.clear()
        return JSONResponse({"ok": True})

    async def staticmap(self, request: Request) -> Response:
        """A synthetic Google Static Maps raster.

        Synthetic on purpose: Google's imagery is not committed to this
        repository, so an offline test cannot replay the real thing. What it
        *can* do is prove the request is well formed and that the raster's
        dimensions match the published contract, which is what every
        coordinate-transform test actually depends on.

        Because the pixels are not Google's, the perceptual hash will not match
        the calibration - so this also exercises the unverified path, which is
        the one that must degrade rather than mislead.
        """
        q = request.query_params
        with self._lock:
            self.requests.append({"endpoint": "staticmap", **dict(q)})

        expected = {
            "zoom": "20",
            "size": "640x640",
            "scale": "2",
            "maptype": "satellite",
        }
        wrong = {k: q.get(k) for k, v in expected.items() if q.get(k) != v}
        if wrong:
            return JSONResponse({"error": "unexpected request", "wrong": wrong}, status_code=400)
        if not (q.get("center") or "").startswith("-34.0465"):
            return JSONResponse({"error": "unexpected centre", "got": q.get("center")}, 400)

        width = int(q["size"].split("x")[0]) * int(q["scale"])
        return Response(content=_synthetic_raster(width), media_type="image/png")

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/maps/api/staticmap", self.staticmap),
                Route("/__stub/health", self.health),
                Route("/__stub/requests", self.request_log),
                Route("/__stub/reset", self.reset, methods=["POST"]),
                Route("/api/v5_3/PVcalc", self.pvcalc),
                Route("/__fault/{fault}/api/v5_3/PVcalc", self.pvcalc),
                Route("/__fault/{fault}/{arg}/api/v5_3/PVcalc", self.pvcalc),
            ]
        )


@lru_cache(maxsize=4)
def _synthetic_raster(width: int) -> bytes:
    """A deterministic stand-in raster of the correct dimensions.

    Structured rather than pure noise - a faint grid - so that a test which
    renders it can see whether the overlay is being stretched, and so that two
    fetches are byte-identical the way Google's own responses are.
    """
    import io

    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(20260730)
    pixels = rng.integers(60, 120, size=(width, width, 3), dtype=np.uint8)
    pixels[::64, :, :] = 200
    pixels[:, ::64, :] = 200

    buffer = io.BytesIO()
    Image.fromarray(pixels, mode="RGB").save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def start_stub(
    *, port: int = 0, captures_dir: Path | None = None
) -> tuple[str, PvgisStub, Callable[[], None]]:
    """Run the stub on a background thread. Returns `(base_url, stub, stop)`.

    Usable from synchronous code - `conftest.py`, the verification scripts - as
    well as from `__main__`, which is what lets all three harnesses share it.
    """
    stub = PvgisStub(captures_dir)
    config = uvicorn.Config(stub.app(), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="pvgis-stub", daemon=True)
    thread.start()

    deadline = time.monotonic() + 30.0
    while not server.started:
        if time.monotonic() > deadline:  # pragma: no cover - CI hang guard
            raise RuntimeError("the PVGIS stub did not start within 30s")
        if not thread.is_alive():  # pragma: no cover - bind failure
            raise RuntimeError("the PVGIS stub thread died during start-up")
        time.sleep(0.01)

    bound = _bound_port(server.servers)

    def stop() -> None:
        server.should_exit = True
        thread.join(timeout=10.0)

    return f"http://127.0.0.1:{bound}", stub, stop


def _bound_port(servers: Iterable[Any]) -> int:
    for served in servers:
        for socket in served.sockets:
            return int(socket.getsockname()[1])
    raise RuntimeError("the PVGIS stub bound no socket")  # pragma: no cover


def write_stub_calibration(destination: Path) -> Path:
    """The committed roof geometry, re-bound to this stub's synthetic raster.

    The verification guard is real in every environment; what differs is which
    imagery each environment can legitimately claim to have been traced on. An
    E2E stack gets a profile for the raster it will actually be served, so it
    exercises the guard rather than a bypass.
    """
    import json

    from app.core.config import get_settings
    from app.domain.imagery import (
        describe_request,
        imagery_sha256,
        perceptual_hash,
        request_signature,
    )
    from app.services.roof import CALIBRATION_PATH

    cfg = get_settings().satellite_image_config
    raster = _synthetic_raster(cfg.source_width_px)

    data = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    data["calibration_metadata"] = {
        **data.get("calibration_metadata", {}),
        "traced_against": "the test stub's synthetic raster",
        "request_signature": request_signature(cfg),
        "request": describe_request(cfg),
        "imagery": {
            "perceptual_hash": perceptual_hash(raster),
            "sha256": imagery_sha256(raster),
        },
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return destination


def main() -> None:  # pragma: no cover - exercised by the E2E launcher
    parser = argparse.ArgumentParser(description="PVGIS replay stub (test infrastructure)")
    parser.add_argument("--port", type=int, default=8102)
    parser.add_argument("--captures", type=Path, default=None)
    parser.add_argument(
        "--write-calibration",
        type=Path,
        default=None,
        help=(
            "Write a roof calibration profile bound to this stub's synthetic "
            "raster. The committed profile is bound to Google's imagery, which "
            "is not in this repository, so an E2E stack needs its own."
        ),
    )
    args = parser.parse_args()

    base_url, stub, stop = start_stub(port=args.port, captures_dir=args.captures)
    print(f"[pvgis-stub] replaying {sorted(stub.captures)} on {base_url}", flush=True)

    if args.write_calibration:
        path = write_stub_calibration(args.write_calibration)
        print(f"[pvgis-stub] wrote a matching roof calibration to {path}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        with contextlib.suppress(Exception):
            stop()


if __name__ == "__main__":  # pragma: no cover
    main()
