import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { App } from './App'
import type { GameDetail } from './services/api'

// vi.mock factories are hoisted above module-scope declarations, so the
// fixture must be created inside vi.hoisted rather than referenced from a
// plain top-level const (which would hit a temporal-dead-zone error).
const { mockGame } = vi.hoisted(() => {
  const mockGame: GameDetail = {
    id: 'abc123',
    white_engine: 'Stockfish',
    black_engine: 'Leela',
    result: '1-0',
    moves: [],
    created_at: '2025-06-15T10:30:00Z',
    opening_name: null,
    sprt_test_id: null,
    start_fen: null,
    time_control: null,
  }
  return { mockGame }
})

// Spread the real module (via importOriginal) rather than replacing it
// outright, so exports GameReplayPage depends on at runtime — notably
// `ApiError`, used in its catch branch — stay real. A factory that omits
// ApiError only "works" by accident: it's invisible today because
// fetchGame never rejects in this file, but the first test that made it
// reject would hit Vitest's "No 'ApiError' export is defined on the mock"
// instead of exercising the intended code path.
vi.mock('./services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./services/api')>()
  return {
    ...actual,
    fetchEngines: vi.fn().mockResolvedValue([]),
    fetchGame: vi.fn().mockResolvedValue(mockGame),
  }
})

describe('App', () => {
  it('renders the Play page by default', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    )
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Play' })).toBeInTheDocument()
    })
  })
})

describe('Routing', () => {
  it('renders Play page at /', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    )
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Play' })).toBeInTheDocument()
    })
  })

  it('renders SPRT Tests page at /sprt', () => {
    render(
      <MemoryRouter initialEntries={['/sprt']}>
        <App />
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { name: 'SPRT Tests' })).toBeInTheDocument()
  })

  it('renders Game Replay page at /games', () => {
    render(
      <MemoryRouter initialEntries={['/games']}>
        <App />
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { name: 'Game Replay' })).toBeInTheDocument()
  })

  it('renders Game Replay page at /games/:id', async () => {
    render(
      <MemoryRouter initialEntries={['/games/abc123']}>
        <App />
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { name: 'Game Replay' })).toBeInTheDocument()
    // fetchGame resolves asynchronously — wait for the resulting state
    // update to settle inside act() before the test (and mock) teardown.
    await waitFor(() => expect(screen.getByText('Stockfish')).toBeInTheDocument())
  })
})
