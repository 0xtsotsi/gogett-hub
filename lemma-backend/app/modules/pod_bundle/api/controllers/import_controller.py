"""Bundle import endpoints — routes land with the import slices."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/pods", tags=["Pod Bundle"], redirect_slashes=False)
