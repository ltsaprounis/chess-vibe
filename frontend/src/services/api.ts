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
 * Parse a non-ok response and throw an {@link ApiError}.
 */
async function handleError(response: Response): Promise<never> {
  let message: string
  try {
    const body: unknown = await response.json()
    message =
      typeof body === 'object' && body !== null && 'detail' in body
        ? String((body as { detail: unknown }).detail)
        : response.statusText
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
 * Fetch the list of available opening books.
 */
export async function fetchOpeningBooks(): Promise<OpeningBook[]> {
  const response = await fetch('/api/openings/books')
  if (!response.ok) await handleError(response)
  return (await response.json()) as OpeningBook[]
}
