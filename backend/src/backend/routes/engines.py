"""Engine listing endpoint.

Returns the engine registry loaded from ``engines.json``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from shared.engine_registry import EngineRegistryError, load_registry
from shared.utils import get_repo_root

from backend.models import EngineResponse

router = APIRouter(prefix="/engines", tags=["engines"])


@router.get("", response_model=list[EngineResponse])
def list_engines() -> list[EngineResponse]:
    """Return all registered engines from ``engines.json``."""
    try:
        entries = load_registry(get_repo_root() / "engines.json")
    except EngineRegistryError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return [EngineResponse(id=e.id, name=e.name) for e in entries]
