/**
 * REST API service layer for the frontend.
 *
 * Centralises all HTTP calls to the FastAPI backend and provides typed
 * async functions. The Vite dev-server proxy handles the base URL so
 * all paths are relative (e.g. `/api/engines`).
 */

import type {
  Engine,
  GameDetail,
  GameFilters,
  GameSummary,
  OpeningBook,
  SPRTTest,
  SPRTTestCreated,
  SPRTTestCreateRequest,
} from '../types/api'

export type {
  Engine,
  GameDetail,
  GameFilters,
  GameSummary,
  Move,
  OpeningBook,
  SPRTTest,
  SPRTTestCreated,
  SPRTTestCreateRequest,
  TimeControl,
} from '../types/api'

/**
 * Error thrown when the backend responds with a non-2xx status.
 */
export class ApiError extends Error {
  public readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/**
 * Request parts FastAPI prefixes a validation `loc` with. They say where the
 * value came from, not which field failed, so they are dropped from the
 * user-facing message: `['body', 'concurrency']` reads as `concurrency`.
 */
const REQUEST_PARTS = new Set(['body', 'query', 'path', 'header', 'cookie'])

/**
 * Render one entry of a FastAPI validation-error array as `field: message`.
 * Returns an empty string for anything without a usable `msg`, so the caller
 * can fall back rather than surfacing a fragment of nothing.
 */
function formatValidationError(entry: unknown): string {
  if (typeof entry === 'string') return entry
  if (typeof entry !== 'object' || entry === null) return ''

  const { loc, msg } = entry as { loc?: unknown; msg?: unknown }
  if (typeof msg !== 'string' || msg === '') return ''

  const path = Array.isArray(loc)
    ? loc
        .filter((part, i) => !(i === 0 && typeof part === 'string' && REQUEST_PARTS.has(part)))
        .join('.')
    : ''
  return path ? `${path}: ${msg}` : msg
}

/**
 * Turn a response body's `detail` into a message worth showing.
 *
 * A 422 from FastAPI carries an *array* of `{loc, msg, type}` objects rather
 * than a string; stringifying that naively yields "[object Object]", which
 * hides which field the backend actually rejected. Returns an empty string
 * when nothing usable can be extracted, leaving the caller to fall back to
 * the HTTP status text.
 */
function formatDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map(formatValidationError).filter(Boolean).join('; ')
  }
  // Preserve the previous stringify for primitives; an object detail has no
  // useful string form, so let the caller fall back instead.
  if (typeof detail === 'number' || typeof detail === 'boolean') return String(detail)
  return ''
}

/**
 * Parse a non-ok response and throw an {@link ApiError}.
 */
async function handleError(response: Response): Promise<never> {
  let message: string
  try {
    const body: unknown = await response.json()
    const detail =
      typeof body === 'object' && body !== null && 'detail' in body
        ? (body as { detail: unknown }).detail
        : undefined
    message = formatDetail(detail) || response.statusText
  } catch {
    message = response.statusText
  }
  throw new ApiError(response.status, message)
}

/**
 * Fetch the list of registered engines.
 */
export async function fetchEngines(): Promise<Engine[]> {
  const response = await fetch('/api/engines')
  if (!response.ok) await handleError(response)
  return (await response.json()) as Engine[]
}

/**
 * Fetch games with optional filters.
 */
export async function fetchGames(filters?: GameFilters): Promise<GameSummary[]> {
  const params = new URLSearchParams()
  if (filters) {
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined) {
        params.set(key, value)
      }
    }
  }
  const query = params.toString()
  const url = query ? `/api/games?${query}` : '/api/games'
  const response = await fetch(url)
  if (!response.ok) await handleError(response)
  return (await response.json()) as GameSummary[]
}

/**
 * Fetch a single game by ID.
 */
export async function fetchGame(id: string): Promise<GameDetail> {
  const response = await fetch(`/api/games/${id}`)
  if (!response.ok) await handleError(response)
  return (await response.json()) as GameDetail
}

/**
 * Create a new SPRT test.
 */
export async function createSPRTTest(body: SPRTTestCreateRequest): Promise<SPRTTestCreated> {
  const response = await fetch('/api/sprt/tests', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) await handleError(response)
  return (await response.json()) as SPRTTestCreated
}

/**
 * Fetch an SPRT test by ID.
 */
export async function fetchSPRTTest(id: string): Promise<SPRTTest> {
  const response = await fetch(`/api/sprt/tests/${id}`)
  if (!response.ok) await handleError(response)
  return (await response.json()) as SPRTTest
}

/**
 * Cancel a running SPRT test.
 */
export async function cancelSPRTTest(id: string): Promise<void> {
  const response = await fetch(`/api/sprt/tests/${id}/cancel`, {
    method: 'POST',
  })
  if (!response.ok) await handleError(response)
}

/**
 * Fetch all SPRT tests.
 */
export async function fetchSPRTTests(): Promise<SPRTTest[]> {
  const response = await fetch('/api/sprt/tests')
  if (!response.ok) await handleError(response)
  return (await response.json()) as SPRTTest[]
}

/**
 * Fetch the list of available opening books.
 */
export async function fetchOpeningBooks(): Promise<OpeningBook[]> {
  const response = await fetch('/api/openings/books')
  if (!response.ok) await handleError(response)
  return (await response.json()) as OpeningBook[]
}
