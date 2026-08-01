"""Aggregate v1 router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import customers, deliveries, health, maps, projects, proposals, roof

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(maps.router)
api_router.include_router(roof.router)
api_router.include_router(customers.router)
api_router.include_router(projects.router)
api_router.include_router(proposals.router)
api_router.include_router(deliveries.router)
