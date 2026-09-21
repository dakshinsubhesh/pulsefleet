"""
PulseFleet — Shared pagination params (Day 5: Read workflows)
"""
from fastapi import Query
from pydantic import BaseModel


class PaginationParams(BaseModel):
    limit: int
    offset: int


def pagination_params(
    limit: int = Query(default=20, ge=1, le=100, description="Max records to return"),
    offset: int = Query(default=0, ge=0, description="Number of records to skip"),
) -> PaginationParams:
    return PaginationParams(limit=limit, offset=offset)
