import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { SPRTTestsPage } from './SPRTTestsPage'
import {
  fetchEngines,
  fetchOpeningBooks,
  fetchSPRTTests,
  createSPRTTest,
  cancelSPRTTest,
} from '../services/api'
import { useWebSocket } from '../hooks/useWebSocket'

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('../services/api', () => ({
  fetchEngines: vi.fn(),
  fetchOpeningBooks: vi.fn(),
  fetchSPRTTests: vi.fn(),
  createSPRTTest: vi.fn(),
  cancelSPRTTest: vi.fn(),
}))

vi.mock('../hooks/useWebSocket', () => ({
  useWebSocket: vi.fn(),
}))

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const mockEngines = [
  { id: 'engine-1', name: 'Stockfish', dir: '/engines/sf', run: './sf' },
  { id: 'engine-2', name: 'Leela', dir: '/engines/lc', run: './lc' },
]

const mockBooks = [
  { id: 'b1', name: 'Default Book', path: '/data/openings/default.pgn', format: 'pgn' },
]

const mockTests = [
  {
    id: 't1',
    engine_a: 'engine-1',
    engine_b: 'engine-2',
    time_control: {
      type: 'movetime',
      movetime_ms: 1000,
      wtime_ms: null,
      btime_ms: null,
      winc_ms: null,
      binc_ms: null,
      moves_to_go: null,
      depth: null,
      nodes: null,
    },
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
  {
    id: 't2',
    engine_a: 'engine-2',
    engine_b: 'engine-1',
    time_control: {
      type: 'movetime',
      movetime_ms: 1000,
      wtime_ms: null,
      btime_ms: null,
      winc_ms: null,
      binc_ms: null,
      moves_to_go: null,
      depth: null,
      nodes: null,
    },
    elo0: 0,
    elo1: 5,
    alpha: 0.05,
    beta: 0.05,
    created_at: '2025-01-01T00:00:00Z',
    status: 'completed',
    wins: 50,
    losses: 30,
    draws: 20,
    llr: 2.95,
    result: 'H1',
    completed_at: '2025-01-01T01:00:00Z',
  },
]

type WsOnMessage = (data: unknown) => void

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('SPRTTestsPage', () => {
  let capturedOnMessage: WsOnMessage | undefined

  beforeEach(() => {
    vi.clearAllMocks()
    capturedOnMessage = undefined

    vi.mocked(fetchEngines).mockResolvedValue(mockEngines)
    vi.mocked(fetchOpeningBooks).mockResolvedValue(mockBooks)
    vi.mocked(fetchSPRTTests).mockResolvedValue(mockTests)

    vi.mocked(useWebSocket).mockImplementation(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (_url: any, options?: any) => {
        capturedOnMessage = options?.onMessage as WsOnMessage | undefined
        return {
          sendMessage: vi.fn() as (data: unknown) => void,
          readyState: _url ? WebSocket.OPEN : WebSocket.CLOSED,
          lastMessage: null,
        }
      },
    )
  })

  // -----------------------------------------------------------------------
  // Initial rendering
  // -----------------------------------------------------------------------

  it('renders the heading', async () => {
    render(<SPRTTestsPage />)
    expect(screen.getByRole('heading', { name: 'SPRT Tests' })).toBeInTheDocument()
    await waitFor(() => expect(fetchSPRTTests).toHaveBeenCalled())
  })

  it('shows loading state initially', () => {
    // Make fetches never resolve to keep loading state
    vi.mocked(fetchEngines).mockReturnValue(new Promise(() => {}))
    vi.mocked(fetchOpeningBooks).mockReturnValue(new Promise(() => {}))
    vi.mocked(fetchSPRTTests).mockReturnValue(new Promise(() => {}))

    render(<SPRTTestsPage />)
    expect(screen.getByText('Loading…')).toBeInTheDocument()
  })

  it('renders test dashboard after loading', async () => {
    render(<SPRTTestsPage />)

    await waitFor(() => {
      expect(screen.getByText('engine-1 vs engine-2')).toBeInTheDocument()
    })

    expect(screen.getByText('engine-2 vs engine-1')).toBeInTheDocument()
    expect(screen.getByText('10 / 3 / 5')).toBeInTheDocument()
    expect(screen.getByText('1.50')).toBeInTheDocument()
    expect(screen.getByText('running')).toBeInTheDocument()
    expect(screen.getByText('completed')).toBeInTheDocument()
    expect(screen.getByText('H1')).toBeInTheDocument()
  })

  it('shows error when data fetch fails', async () => {
    vi.mocked(fetchEngines).mockRejectedValue(new Error('Network error'))

    render(<SPRTTestsPage />)

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
    })
    expect(screen.getByText('Network error')).toBeInTheDocument()
  })

  // -----------------------------------------------------------------------
  // Create form
  // -----------------------------------------------------------------------

  it('toggles create form when New Test button is clicked', async () => {
    render(<SPRTTestsPage />)

    await waitFor(() => expect(screen.getByText('New Test')).toBeInTheDocument())

    await userEvent.click(screen.getByText('New Test'))
    expect(screen.getByText('Create SPRT Test')).toBeInTheDocument()
    expect(screen.getByLabelText('Engine A')).toBeInTheDocument()
    expect(screen.getByLabelText('Engine B')).toBeInTheDocument()
    expect(screen.getByLabelText('Time Control')).toBeInTheDocument()
    expect(screen.getByLabelText('Elo0')).toBeInTheDocument()
    expect(screen.getByLabelText('Elo1')).toBeInTheDocument()
    expect(screen.getByLabelText('Alpha')).toBeInTheDocument()
    expect(screen.getByLabelText('Beta')).toBeInTheDocument()
    expect(screen.getByLabelText('Opening Book')).toBeInTheDocument()
    expect(screen.getByLabelText('Concurrency')).toBeInTheDocument()

    await userEvent.click(screen.getByText('Close Form'))
    expect(screen.queryByText('Create SPRT Test')).not.toBeInTheDocument()
  })

  it('populates engine dropdowns with fetched engines', async () => {
    render(<SPRTTestsPage />)
    await waitFor(() => expect(screen.getByText('New Test')).toBeInTheDocument())

    await userEvent.click(screen.getByText('New Test'))

    const engineASelect = screen.getByLabelText('Engine A')
    expect(engineASelect).toBeInTheDocument()
    expect(screen.getAllByText('Stockfish').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Leela').length).toBeGreaterThanOrEqual(1)
  })

  it('populates opening book dropdown', async () => {
    render(<SPRTTestsPage />)
    await waitFor(() => expect(screen.getByText('New Test')).toBeInTheDocument())

    await userEvent.click(screen.getByText('New Test'))
    expect(screen.getByText('Default Book')).toBeInTheDocument()
  })

  // -----------------------------------------------------------------------
  // Form validation
  // -----------------------------------------------------------------------

  it('shows validation errors when engines are not selected', async () => {
    render(<SPRTTestsPage />)
    await waitFor(() => expect(screen.getByText('New Test')).toBeInTheDocument())

    await userEvent.click(screen.getByText('New Test'))
    await userEvent.click(screen.getByText('Create Test'))

    await waitFor(() => {
      expect(screen.getByText('Engine A is required')).toBeInTheDocument()
      expect(screen.getByText('Engine B is required')).toBeInTheDocument()
    })
  })

  it('shows validation error when elo1 <= elo0', async () => {
    render(<SPRTTestsPage />)
    await waitFor(() => expect(screen.getByText('New Test')).toBeInTheDocument())

    await userEvent.click(screen.getByText('New Test'))

    // Select engines first
    await userEvent.selectOptions(screen.getByLabelText('Engine A'), 'engine-1')
    await userEvent.selectOptions(screen.getByLabelText('Engine B'), 'engine-2')

    // Set elo1 <= elo0
    const elo1Input = screen.getByLabelText('Elo1')
    await userEvent.clear(elo1Input)
    await userEvent.type(elo1Input, '0')

    await userEvent.click(screen.getByText('Create Test'))

    await waitFor(() => {
      expect(screen.getByText('Elo1 must be greater than Elo0')).toBeInTheDocument()
    })
  })

  it('shows validation error for invalid alpha/beta', async () => {
    render(<SPRTTestsPage />)
    await waitFor(() => expect(screen.getByText('New Test')).toBeInTheDocument())

    await userEvent.click(screen.getByText('New Test'))

    await userEvent.selectOptions(screen.getByLabelText('Engine A'), 'engine-1')
    await userEvent.selectOptions(screen.getByLabelText('Engine B'), 'engine-2')

    const alphaInput = screen.getByLabelText('Alpha')
    await userEvent.clear(alphaInput)
    await userEvent.type(alphaInput, '0')

    await userEvent.click(screen.getByText('Create Test'))

    await waitFor(() => {
      expect(screen.getByText('Alpha must be between 0 and 1')).toBeInTheDocument()
    })
  })

  // -----------------------------------------------------------------------
  // Form submission
  // -----------------------------------------------------------------------

  it('submits correct API request on valid form', async () => {
    vi.mocked(createSPRTTest).mockResolvedValue({ id: 'new-t1', status: 'running' })

    render(<SPRTTestsPage />)
    await waitFor(() => expect(screen.getByText('New Test')).toBeInTheDocument())

    await userEvent.click(screen.getByText('New Test'))

    await userEvent.selectOptions(screen.getByLabelText('Engine A'), 'engine-1')
    await userEvent.selectOptions(screen.getByLabelText('Engine B'), 'engine-2')

    await userEvent.click(screen.getByText('Create Test'))

    await waitFor(() => {
      expect(createSPRTTest).toHaveBeenCalledWith({
        engine_a: 'engine-1',
        engine_b: 'engine-2',
        time_control: 'movetime=1000',
        elo0: 0,
        elo1: 5,
        alpha: 0.05,
        beta: 0.05,
        book_path: null,
        concurrency: 1,
      })
    })
  })

  it('closes form after successful submission', async () => {
    vi.mocked(createSPRTTest).mockResolvedValue({ id: 'new-t1', status: 'running' })

    render(<SPRTTestsPage />)
    await waitFor(() => expect(screen.getByText('New Test')).toBeInTheDocument())

    await userEvent.click(screen.getByText('New Test'))
    await userEvent.selectOptions(screen.getByLabelText('Engine A'), 'engine-1')
    await userEvent.selectOptions(screen.getByLabelText('Engine B'), 'engine-2')
    await userEvent.click(screen.getByText('Create Test'))

    await waitFor(() => {
      expect(screen.queryByText('Create SPRT Test')).not.toBeInTheDocument()
    })
  })

  it('shows error when submission fails', async () => {
    vi.mocked(createSPRTTest).mockRejectedValue(new Error('Server error'))

    render(<SPRTTestsPage />)
    await waitFor(() => expect(screen.getByText('New Test')).toBeInTheDocument())

    await userEvent.click(screen.getByText('New Test'))
    await userEvent.selectOptions(screen.getByLabelText('Engine A'), 'engine-1')
    await userEvent.selectOptions(screen.getByLabelText('Engine B'), 'engine-2')
    await userEvent.click(screen.getByText('Create Test'))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
      expect(screen.getByText('Server error')).toBeInTheDocument()
    })
  })

  // -----------------------------------------------------------------------
  // Cancel test
  // -----------------------------------------------------------------------

  it('cancels a running test', async () => {
    vi.mocked(cancelSPRTTest).mockResolvedValue(undefined)

    render(<SPRTTestsPage />)
    await waitFor(() => {
      expect(screen.getByText('engine-1 vs engine-2')).toBeInTheDocument()
    })

    const cancelButton = screen.getByRole('button', { name: 'Cancel' })
    await userEvent.click(cancelButton)

    expect(cancelSPRTTest).toHaveBeenCalledWith('t1')

    await waitFor(() => {
      // The running test should now show as cancelled
      expect(screen.getByText('cancelled')).toBeInTheDocument()
    })
  })

  it('shows error when cancel fails', async () => {
    vi.mocked(cancelSPRTTest).mockRejectedValue(new Error('Cancel failed'))

    render(<SPRTTestsPage />)
    await waitFor(() => {
      expect(screen.getByText('engine-1 vs engine-2')).toBeInTheDocument()
    })

    const cancelButton = screen.getByRole('button', { name: 'Cancel' })
    await userEvent.click(cancelButton)

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
      expect(screen.getByText('Cancel failed')).toBeInTheDocument()
    })
  })

  // -----------------------------------------------------------------------
  // WebSocket live progress
  // -----------------------------------------------------------------------

  it('displays live progress when a running test is selected', async () => {
    render(<SPRTTestsPage />)

    await waitFor(() => {
      expect(screen.getByText('engine-1 vs engine-2')).toBeInTheDocument()
    })

    // Click the running test row
    await userEvent.click(screen.getByText('10 / 3 / 5'))

    // Simulate WS progress message
    act(() => {
      capturedOnMessage?.({
        type: 'progress',
        wins: 15,
        losses: 7,
        draws: 5,
        llr: 1.8,
        lower_bound: -2.94,
        upper_bound: 2.94,
      })
    })

    await waitFor(() => {
      expect(screen.getByText('15')).toBeInTheDocument()
      expect(screen.getByText('7')).toBeInTheDocument()
      expect(screen.getByText('5')).toBeInTheDocument()
      expect(screen.getByText('Wins')).toBeInTheDocument()
      expect(screen.getByText('Draws')).toBeInTheDocument()
      expect(screen.getByText('Losses')).toBeInTheDocument()
    })

    // Check LLR bar
    expect(screen.getByRole('progressbar')).toBeInTheDocument()
  })

  it('displays complete result when test finishes', async () => {
    render(<SPRTTestsPage />)

    await waitFor(() => {
      expect(screen.getByText('engine-1 vs engine-2')).toBeInTheDocument()
    })

    // Click the running test row
    await userEvent.click(screen.getByText('10 / 3 / 5'))

    // First send a progress message to establish the panel
    act(() => {
      capturedOnMessage?.({
        type: 'progress',
        wins: 15,
        losses: 7,
        draws: 5,
        llr: 1.8,
        lower_bound: -2.94,
        upper_bound: 2.94,
      })
    })

    // Then send complete
    act(() => {
      capturedOnMessage?.({
        type: 'complete',
        result: 'H1',
        total_games: 100,
        llr: 2.95,
      })
    })

    await waitFor(() => {
      expect(screen.getByText(/Result: H1/)).toBeInTheDocument()
      expect(screen.getByText(/100 games/)).toBeInTheDocument()
    })
  })

  it('shows error from WebSocket error message', async () => {
    render(<SPRTTestsPage />)

    await waitFor(() => {
      expect(screen.getByText('engine-1 vs engine-2')).toBeInTheDocument()
    })

    await userEvent.click(screen.getByText('10 / 3 / 5'))

    act(() => {
      capturedOnMessage?.({
        type: 'error',
        message: 'Engine crashed',
      })
    })

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
      expect(screen.getByText('Engine crashed')).toBeInTheDocument()
    })
  })

  // -----------------------------------------------------------------------
  // Empty state
  // -----------------------------------------------------------------------

  it('shows empty state when no tests exist', async () => {
    vi.mocked(fetchSPRTTests).mockResolvedValue([])

    render(<SPRTTestsPage />)

    await waitFor(() => {
      expect(screen.getByText('No SPRT tests yet.')).toBeInTheDocument()
    })
  })
})
