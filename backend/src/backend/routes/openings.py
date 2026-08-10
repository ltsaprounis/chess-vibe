"""Opening book listing and upload endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, UploadFile
from shared.storage.repository import OpeningBookRepository

from backend.models import OpeningBookResponse, OpeningBookUploadResponse

router = APIRouter(prefix="/openings/books", tags=["openings"])

# An opening book is a list of positions; the largest realistic EPD or PGN
# suite is a few megabytes, so this is generous rather than restrictive.
MAX_BOOK_UPLOAD_BYTES = 32 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024

# The uploaded filename is now persisted and echoed back as the book's
# display name, so it is client-controlled text of unbounded length. It
# never reaches the filesystem — books are stored as ``{uuid}.{format}`` —
# so this bounds what gets stored, nothing more.
MAX_BOOK_NAME_CHARS = 255


async def _read_capped(file: UploadFile, limit: int) -> bytes:
    """Read *file* into memory, refusing to accumulate more than *limit* bytes.

    Read in chunks and abort on the first one that crosses the limit, so the
    excess beyond *limit* is never accumulated. Two honest limits on what
    that buys:

    - It bounds this handler's own allocation only. Starlette has already
      spooled the part to a temporary file while parsing the multipart body,
      before any route code runs, and a handler cannot cap that.
    - A *legal* upload still peaks at roughly twice its size, because the
      accumulated chunks and the joined result are both live for a moment.
      At a 32 MiB cap that is a bounded ~64 MiB, which is the price of
      handing ``bytes`` to the repository ABC.

    Args:
        file: The uploaded file.
        limit: Maximum number of bytes to accept.

    Returns:
        The file's full content.

    Raises:
        HTTPException: 413 if the content exceeds *limit*.
    """
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(UPLOAD_CHUNK_BYTES):
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail="Opening book is too large")
        chunks.append(chunk)
    return b"".join(chunks)


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
        OpeningBookResponse(id=b.id, name=b.name, format=b.format) for b in book_repo.list_books()
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

    Raises:
        HTTPException: 400 for a missing filename or unsupported
            extension, 413 if the file exceeds the upload cap.
    """
    if file.filename is None:
        raise HTTPException(status_code=400, detail="No filename provided")

    if not file.filename.endswith((".pgn", ".epd")):
        raise HTTPException(status_code=400, detail="Only .pgn and .epd files are supported")

    book_repo: OpeningBookRepository = request.app.state.book_repo
    content = await _read_capped(file, MAX_BOOK_UPLOAD_BYTES)
    suffix = file.filename.rsplit(".", 1)[-1]
    book = book_repo.save_book(
        name=file.filename[:MAX_BOOK_NAME_CHARS], content=content, format=suffix
    )

    return OpeningBookUploadResponse(
        id=book.id,
        name=book.name,
        format=book.format,
    )
