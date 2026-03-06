"""SPRT test management endpoints.

Create, query, and cancel SPRT tests. Tests run as background
subprocesses managed by :class:`backend.services.sprt_service.SPRTService`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from shared.storage.models import SPRTStatus, SPRTTestFilter

from backend.converters import sprt_test_to_response
from backend.models import SPRTTestCreatedResponse, SPRTTestCreateRequest, SPRTTestResponse

router = APIRouter(prefix="/sprt/tests", tags=["sprt"])


@router.get("", response_model=list[SPRTTestResponse])
def list_sprt_tests(
    request: Request,
    status: str | None = None,
    engine_id: str | None = None,
) -> list[SPRTTestResponse]:
    """List all SPRT tests with optional filtering.

    Args:
        request: FastAPI request.
        status: Filter by test status (``running``, ``completed``, ``cancelled``).
        engine_id: Filter by engine (matches either engine_a or engine_b).

    Returns:
        List of SPRT tests matching the filter criteria.
    """
    sprt_filter: SPRTTestFilter | None = None
    if status is not None or engine_id is not None:
        parsed_status: SPRTStatus | None = None
        if status is not None:
            try:
                parsed_status = SPRTStatus(status)
            except ValueError as err:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid status '{status}'. "
                    "Must be one of: running, completed, cancelled",
                ) from err
        sprt_filter = SPRTTestFilter(status=parsed_status, engine_id=engine_id)

    repo = request.app.state.sprt_repo
    tests = repo.list_sprt_tests(sprt_filter)
    return [sprt_test_to_response(t) for t in tests]


@router.post("", response_model=SPRTTestCreatedResponse, status_code=201)
async def create_sprt_test(
    body: SPRTTestCreateRequest,
    request: Request,
) -> SPRTTestCreatedResponse:
    """Start a new SPRT test.

    Launches the SPRT runner as a background subprocess.

    Args:
        body: Test configuration.
        request: FastAPI request.

    Returns:
        The new test ID and initial status.
    """
    sprt_service = request.app.state.sprt_service
    try:
        test_id = await sprt_service.start_test(
            engine_a=body.engine_a,
            engine_b=body.engine_b,
            time_control_str=body.time_control,
            elo0=body.elo0,
            elo1=body.elo1,
            alpha=body.alpha,
            beta=body.beta,
            book_path=body.book_path,
            concurrency=body.concurrency,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return SPRTTestCreatedResponse(id=test_id, status="running")


@router.get("/{test_id}", response_model=SPRTTestResponse)
def get_sprt_test(test_id: str, request: Request) -> SPRTTestResponse:
    """Retrieve SPRT test status.

    Args:
        test_id: Unique test identifier.
        request: FastAPI request.

    Returns:
        Test metadata including LLR, W/D/L, and status.
    """
    test = request.app.state.sprt_repo.get_sprt_test(test_id)
    if test is None:
        raise HTTPException(status_code=404, detail=f"SPRT test '{test_id}' not found")
    return sprt_test_to_response(test)


@router.post("/{test_id}/cancel", status_code=200)
async def cancel_sprt_test(test_id: str, request: Request) -> dict[str, str]:
    """Cancel a running SPRT test by sending SIGTERM.

    Args:
        test_id: Unique test identifier.
        request: FastAPI request.

    Returns:
        Confirmation message.
    """
    sprt_service = request.app.state.sprt_service
    sent = await sprt_service.cancel_test(test_id)
    if not sent:
        raise HTTPException(status_code=404, detail=f"No running test with id '{test_id}'")
    return {"status": "cancelled"}
