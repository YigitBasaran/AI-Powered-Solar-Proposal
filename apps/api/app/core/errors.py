"""Structured application errors.

Every failure the client can see is shaped identically:

    {"error": {"code": ..., "message": ..., "details": {...}, "requestId": ...}}

so the frontend never has to guess whether a response body is an error.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for errors that map onto a structured HTTP response."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message
        self.details: dict[str, Any] = details or {}

    def to_payload(self, request_id: str) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
                "requestId": request_id,
            }
        }


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = 404
    message = "The requested resource does not exist."


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    status_code = 422
    message = "The request could not be validated."


class InvalidStepTransitionError(AppError):
    code = "INVALID_STEP_TRANSITION"
    status_code = 409
    message = "That action is not available at the current workflow step."


class MapsUnavailableError(AppError):
    code = "MAPS_UNAVAILABLE"
    status_code = 502
    message = "The satellite image could not be retrieved."


class RoofCalibrationMissingError(AppError):
    code = "ROOF_CALIBRATION_MISSING"
    status_code = 500
    message = "The fixed roof calibration data is missing or invalid."


class InsufficientRoofCapacityError(AppError):
    code = "INSUFFICIENT_ROOF_CAPACITY"
    status_code = 409
    message = "The roof cannot physically accommodate any panels."


class PvgisUnavailableError(AppError):
    code = "PVGIS_UNAVAILABLE"
    status_code = 502
    message = "Solar production data could not be retrieved from PVGIS."


class PvgisInconsistentProvenanceError(AppError):
    """The facet probes did not all come from the same radiation database.

    Distinct from `PVGIS_UNAVAILABLE` because the service answered perfectly
    well - it just answered from more than one dataset, and the optimiser
    compares the four specific yields directly, so they have to be comparable.
    An operator reading the log needs to know it was a consistency problem
    rather than an outage; they are fixed differently.
    """

    code = "PVGIS_INCONSISTENT_PROVENANCE"
    status_code = 502
    message = (
        "PVGIS answered from more than one radiation database, so the facet "
        "yields cannot be compared."
    )


class FxRateUnavailableError(AppError):
    code = "FX_RATE_UNAVAILABLE"
    status_code = 502
    message = "The USD/EUR reference rate could not be retrieved."


class ProposalIncompleteError(AppError):
    code = "PROPOSAL_INCOMPLETE"
    status_code = 409
    message = "The proposal cannot be finalised until the analysis is complete."


class LlmUnavailableError(AppError):
    code = "LLM_UNAVAILABLE"
    status_code = 502
    message = "The local language model is not available."
