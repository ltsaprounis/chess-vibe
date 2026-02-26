"""Opening book listing and upload endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, UploadFile

from backend.models import OpeningBookResponse, OpeningBookUploadResponse

router = APIRouter(prefix="/openings/books", tags=["openings"])


@router.get("", response_model=list[OpeningBookResponse])
def list_opening_books(request: Request) -> list[OpeningBookResponse]:
    """List available opening books.

    Opening books are stored as files in the ``data/openings/`` directory.

    Args:
        request: FastAPI request.

    Returns:
        List of opening book descriptors.
    """
    books_dir = request.app.state.data_dir / "openings"
    if not books_dir.is_dir():
        return []

    books: list[OpeningBookResponse] = []
    for path in sorted(books_dir.iterdir()):
        if path.is_file() and path.suffix in (".pgn", ".epd"):
            books.append(
                OpeningBookResponse(
                    id=path.stem,
                    name=path.stem,
                    path=str(path),
                    format=path.suffix.lstrip("."),
                )
            )
    return books


@router.post("", response_model=OpeningBookUploadResponse, status_code=201)
async def upload_opening_book(
    request: Request,
    file: UploadFile,
) -> OpeningBookUploadResponse:
    """Upload an opening book file (PGN or EPD).

    Args:
        request: FastAPI request.
        file: Uploaded file.

    Returns:
        Descriptor of the saved opening book.
    """
    if file.filename is None:
        raise HTTPException(status_code=400, detail="No filename provided")

    if not file.filename.endswith((".pgn", ".epd")):
        raise HTTPException(status_code=400, detail="Only .pgn and .epd files are supported")

    books_dir = request.app.state.data_dir / "openings"
    books_dir.mkdir(parents=True, exist_ok=True)

    book_id = str(uuid.uuid4())
    suffix = file.filename.rsplit(".", 1)[-1]
    dest = books_dir / f"{book_id}.{suffix}"

    content = await file.read()
    dest.write_bytes(content)

    return OpeningBookUploadResponse(
        id=book_id,
        name=file.filename,
        path=str(dest),
        format=suffix,
    )
