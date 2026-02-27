"""WebSocket endpoint for live SPRT progress streaming.

Subscribes to the SPRT service's update queue for a given test and
forwards JSON messages to the connected WebSocket client.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.services.sprt_service import SPRTService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/sprt/{test_id}")
async def sprt_progress_websocket(websocket: WebSocket, test_id: str) -> None:
    """Stream live SPRT test progress updates.

    Connects to the SPRT service's subscriber queue and forwards
    each JSON-lines message (progress, game_result, complete, error)
    to the client.

    The WebSocket closes when the test completes or the client
    disconnects.
    """
    await websocket.accept()

    sprt_service: SPRTService = websocket.app.state.sprt_service
    queue = sprt_service.subscribe(test_id)

    if queue is None:
        await websocket.send_json({"type": "error", "message": f"Test '{test_id}' is not running"})
        await websocket.close()
        return

    try:
        while True:
            msg: dict[str, Any] = await queue.get()
            await websocket.send_json(msg)

            if msg.get("type") == "complete":
                break
    except WebSocketDisconnect:
        logger.info("SPRT WebSocket disconnected for test %s", test_id)
    except Exception:
        logger.exception("Error in SPRT WebSocket for test %s", test_id)
    finally:
        sprt_service.unsubscribe(test_id, queue)
