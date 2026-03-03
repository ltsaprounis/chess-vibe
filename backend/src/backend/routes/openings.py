"""Opening book listing and upload endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, UploadFile
from shared.storage.repository import OpeningBookRepository

from backend.models import OpeningBookResponse, OpeningBookUploadResponse

router = APIRouter(prefix="/openings/books", tags=["openings"])


@router.get("", response_model=list[OpeningBookResponse])
def list_opening_books(request: Request) -> list[OpeningBookResponse]:
    """List available opening books.

    Args:
        request: FastAPI request.

    Returns:
        List of opening book descriptors.
    """
    book_repo: OpeningBookRepository = request.app.state.book_repo
    return [
        OpeningBookResponse(id=b.id, name=b.name, path=b.path, format=b.format)
        for b in book_repo.list_books()
    ]


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

    book_repo: OpeningBookRepository = request.app.state.book_repo
    content = await file.read()
    suffix = file.filename.rsplit(".", 1)[-1]
    book = book_repo.save_book(name=file.filename, content=content, format=suffix)

    return OpeningBookUploadResponse(
        id=book.id,
        name=book.name,
        path=book.path,
        format=book.format,
    )
