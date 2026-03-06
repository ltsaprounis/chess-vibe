import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  fetchEngines,
  fetchGames,
  fetchGame,
  createSPRTTest,
  fetchSPRTTest,
  fetchSPRTTests,
  cancelSPRTTest,
  fetchOpeningBooks,
  ApiError,
} from '../api'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: 'OK',
    json: () => Promise.resolve(body),
  } as unknown as Response
}

function errorResponse(status: number, detail: string): Response {
  return {
    ok: false,
    status,
    statusText: 'Not Found',
    json: () => Promise.resolve({ detail }),
  } as unknown as Response
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

const mockFetch = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>()

beforeEach(() => {
  mockFetch.mockReset()
  globalThis.fetch = mockFetch
})

// ---------------------------------------------------------------------------
// fetchEngines
// ---------------------------------------------------------------------------

describe('fetchEngines', () => {
  it('calls GET /api/engines and returns engines', async () => {
    const engines = [{ id: 'e1', name: 'Engine 1', dir: '/engines/e1', run: './run' }]
    mockFetch.mockResolvedValueOnce(jsonResponse(engines))

    const result = await fetchEngines()

    expect(mockFetch).toHaveBeenCalledWith('/api/engines')
    expect(result).toEqual(engines)
  })

  it('throws ApiError on non-2xx response', async () => {
    mockFetch.mockResolvedValueOnce(errorResponse(500, 'Registry error'))

    await expect(fetchEngines()).rejects.toThrow(ApiError)
    await mockFetch.mockResolvedValueOnce(errorResponse(500, 'Registry error'))
    try {
      await fetchEngines()
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError)
      expect((e as ApiError).status).toBe(500)
      expect((e as ApiError).message).toBe('Registry error')
    }
  })
})

// ---------------------------------------------------------------------------
// fetchGames
// ---------------------------------------------------------------------------

describe('fetchGames', () => {
  it('calls GET /api/games without filters', async () => {
    const games = [
      {
        id: 'g1',
        white_engine: 'e1',
        black_engine: 'e2',
        result: '1-0',
        move_count: 40,
        created_at: '2025-01-01T00:00:00Z',
        opening_name: null,
        sprt_test_id: null,
      },
    ]
    mockFetch.mockResolvedValueOnce(jsonResponse(games))

    const result = await fetchGames()

    expect(mockFetch).toHaveBeenCalledWith('/api/games')
    expect(result).toEqual(games)
  })

  it('appends query params when filters are provided', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse([]))

    await fetchGames({ sprt_test_id: 't1', engine_id: 'e1' })

    const url = mockFetch.mock.calls[0][0] as string
    expect(url).toContain('/api/games?')
    expect(url).toContain('sprt_test_id=t1')
    expect(url).toContain('engine_id=e1')
  })

  it('omits undefined filter values from query string', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse([]))

    await fetchGames({ result: '1-0' })

    const url = mockFetch.mock.calls[0][0] as string
    expect(url).toBe('/api/games?result=1-0')
  })

  it('throws ApiError on error', async () => {
    mockFetch.mockResolvedValueOnce(errorResponse(400, 'Invalid result: bad'))

    await expect(fetchGames({ result: 'bad' })).rejects.toThrow(ApiError)
  })
})

// ---------------------------------------------------------------------------
// fetchGame
// ---------------------------------------------------------------------------

describe('fetchGame', () => {
  it('calls GET /api/games/{id} and returns game detail', async () => {
    const game = {
      id: 'g1',
      white_engine: 'e1',
      black_engine: 'e2',
      result: '1-0',
      moves: [],
      created_at: '2025-01-01T00:00:00Z',
      opening_name: null,
      sprt_test_id: null,
      start_fen: null,
      time_control: null,
    }
    mockFetch.mockResolvedValueOnce(jsonResponse(game))

    const result = await fetchGame('g1')

    expect(mockFetch).toHaveBeenCalledWith('/api/games/g1')
    expect(result).toEqual(game)
  })

  it('throws ApiError when game not found', async () => {
    mockFetch.mockResolvedValueOnce(errorResponse(404, "Game 'g1' not found"))

    try {
      await fetchGame('g1')
      expect.fail('Should have thrown')
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError)
      expect((e as ApiError).status).toBe(404)
      expect((e as ApiError).message).toBe("Game 'g1' not found")
    }
  })
})

// ---------------------------------------------------------------------------
// createSPRTTest
// ---------------------------------------------------------------------------

describe('createSPRTTest', () => {
  it('calls POST /api/sprt/tests with JSON body', async () => {
    const created = { id: 't1', status: 'running' }
    mockFetch.mockResolvedValueOnce(jsonResponse(created, 201))

    const body = { engine_a: 'e1', engine_b: 'e2', time_control: 'movetime=1000' }
    const result = await createSPRTTest(body)

    expect(mockFetch).toHaveBeenCalledWith('/api/sprt/tests', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    expect(result).toEqual(created)
  })

  it('throws ApiError on server error', async () => {
    mockFetch.mockResolvedValueOnce(errorResponse(500, 'Engine not found'))

    await expect(
      createSPRTTest({ engine_a: 'e1', engine_b: 'e2', time_control: 'movetime=1000' }),
    ).rejects.toThrow(ApiError)
  })
})

// ---------------------------------------------------------------------------
// fetchSPRTTest
// ---------------------------------------------------------------------------

describe('fetchSPRTTest', () => {
  it('calls GET /api/sprt/tests/{id} and returns test', async () => {
    const test = {
      id: 't1',
      engine_a: 'e1',
      engine_b: 'e2',
      time_control: { type: 'movetime', movetime_ms: 1000 },
      elo0: 0,
      elo1: 5,
      alpha: 0.05,
      beta: 0.05,
      created_at: '2025-01-01T00:00:00Z',
      status: 'running',
      wins: 10,
      losses: 5,
      draws: 3,
      llr: 1.5,
      result: null,
      completed_at: null,
    }
    mockFetch.mockResolvedValueOnce(jsonResponse(test))

    const result = await fetchSPRTTest('t1')

    expect(mockFetch).toHaveBeenCalledWith('/api/sprt/tests/t1')
    expect(result).toEqual(test)
  })

  it('throws ApiError when test not found', async () => {
    mockFetch.mockResolvedValueOnce(errorResponse(404, "SPRT test 't1' not found"))

    await expect(fetchSPRTTest('t1')).rejects.toThrow(ApiError)
  })
})

// ---------------------------------------------------------------------------
// cancelSPRTTest
// ---------------------------------------------------------------------------

describe('cancelSPRTTest', () => {
  it('calls POST /api/sprt/tests/{id}/cancel', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ status: 'cancelled' }))

    await cancelSPRTTest('t1')

    expect(mockFetch).toHaveBeenCalledWith('/api/sprt/tests/t1/cancel', {
      method: 'POST',
    })
  })

  it('throws ApiError when test not found', async () => {
    mockFetch.mockResolvedValueOnce(errorResponse(404, "No running test with id 't1'"))

    await expect(cancelSPRTTest('t1')).rejects.toThrow(ApiError)
  })
})

// ---------------------------------------------------------------------------
// fetchSPRTTests
// ---------------------------------------------------------------------------

describe('fetchSPRTTests', () => {
  it('calls GET /api/sprt/tests and returns tests', async () => {
    const tests = [
      {
        id: 't1',
        engine_a: 'e1',
        engine_b: 'e2',
        time_control: { type: 'movetime', movetime_ms: 1000 },
        elo0: 0,
        elo1: 5,
        alpha: 0.05,
        beta: 0.05,
        created_at: '2025-01-01T00:00:00Z',
        status: 'running',
        wins: 10,
        losses: 5,
        draws: 3,
        llr: 1.5,
        result: null,
        completed_at: null,
      },
    ]
    mockFetch.mockResolvedValueOnce(jsonResponse(tests))

    const result = await fetchSPRTTests()

    expect(mockFetch).toHaveBeenCalledWith('/api/sprt/tests')
    expect(result).toEqual(tests)
  })

  it('throws ApiError on error', async () => {
    mockFetch.mockResolvedValueOnce(errorResponse(500, 'Internal error'))

    await expect(fetchSPRTTests()).rejects.toThrow(ApiError)
  })
})

// ---------------------------------------------------------------------------
// fetchOpeningBooks
// ---------------------------------------------------------------------------

describe('fetchOpeningBooks', () => {
  it('calls GET /api/openings/books and returns books', async () => {
    const books = [{ id: 'b1', name: 'default', path: '/data/openings/b1.pgn', format: 'pgn' }]
    mockFetch.mockResolvedValueOnce(jsonResponse(books))

    const result = await fetchOpeningBooks()

    expect(mockFetch).toHaveBeenCalledWith('/api/openings/books')
    expect(result).toEqual(books)
  })

  it('throws ApiError on error', async () => {
    mockFetch.mockResolvedValueOnce(errorResponse(500, 'Internal error'))

    await expect(fetchOpeningBooks()).rejects.toThrow(ApiError)
  })
})

// ---------------------------------------------------------------------------
// Error handling edge cases
// ---------------------------------------------------------------------------

describe('ApiError handling', () => {
  it('uses statusText when response body has no detail field', async () => {
    const response = {
      ok: false,
      status: 502,
      statusText: 'Bad Gateway',
      json: () => Promise.resolve({ error: 'something' }),
    } as unknown as Response
    mockFetch.mockResolvedValueOnce(response)

    try {
      await fetchEngines()
      expect.fail('Should have thrown')
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError)
      expect((e as ApiError).message).toBe('Bad Gateway')
    }
  })

  it('uses statusText when response body is not JSON', async () => {
    const response = {
      ok: false,
      status: 503,
      statusText: 'Service Unavailable',
      json: () => Promise.reject(new Error('not json')),
    } as unknown as Response
    mockFetch.mockResolvedValueOnce(response)

    try {
      await fetchEngines()
      expect.fail('Should have thrown')
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError)
      expect((e as ApiError).message).toBe('Service Unavailable')
    }
  })
})
