import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { GameReplayPage } from './GameReplayPage'
import { fetchGame, ApiError } from '../services/api'
import type { GameDetail } from '../services/api'

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>()
  return {
    ...actual,
    fetchGame: vi.fn(),
  }
})

vi.mock('react-chessboard', () => ({
  Chessboard: ({ options }: { options?: Record<string, unknown> }) => (
    <div
      data-testid="chessboard"
      data-position={String(options?.position ?? '')}
      data-orientation={String(options?.boardOrientation ?? 'white')}
    />
  ),
}))

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STANDARD_INITIAL_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'

function renderAtPath(path: string): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="games/:id?" element={<GameReplayPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

// Ruy Lopez opening: 1. e4 e5 2. Nf3 Nc6 3. Bb5
// Sign convention under test: score_cp/score_mate on a Move are stored from
// the perspective of whichever side just moved. White moves live at even
// indices (already White-relative); Black moves live at odd indices (must be
// negated to read as White-relative). Move 3 (Nc6, black, index 3) carries no
// recorded eval at all. Move 4 (Bb5, white, index 4) is a forced mate.
const sampleGame: GameDetail = {
  id: 'game-1',
  white_engine: 'Stockfish',
  black_engine: 'Leela',
  result: '1-0',
  moves: [
    {
      uci: 'e2e4',
      san: 'e4',
      fen_after: 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1',
      score_cp: 20,
      score_mate: null,
      depth: 18,
      seldepth: 22,
      pv: ['e7e5', 'g1f3', 'b8c6'],
      nodes: 100000,
      time_ms: 500,
      clock_white_ms: null,
      clock_black_ms: null,
    },
    {
      uci: 'e7e5',
      san: 'e5',
      fen_after: 'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2',
      score_cp: 10,
      score_mate: null,
      depth: 18,
      seldepth: 20,
      pv: ['g1f3', 'b8c6', 'f1b5'],
      nodes: 90000,
      time_ms: 480,
      clock_white_ms: null,
      clock_black_ms: null,
    },
    {
      uci: 'g1f3',
      san: 'Nf3',
      fen_after: 'rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2',
      score_cp: 30,
      score_mate: null,
      depth: 19,
      seldepth: 24,
      pv: ['b8c6', 'f1b5', 'a7a6'],
      nodes: 120000,
      time_ms: 510,
      clock_white_ms: null,
      clock_black_ms: null,
    },
    {
      uci: 'b8c6',
      san: 'Nc6',
      fen_after: 'rnbqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3',
      score_cp: null,
      score_mate: null,
      depth: null,
      seldepth: null,
      pv: [],
      nodes: null,
      time_ms: null,
      clock_white_ms: null,
      clock_black_ms: null,
    },
    {
      uci: 'f1b5',
      san: 'Bb5',
      fen_after: 'rnbqkbnr/pppp1ppp/2n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3',
      score_cp: null,
      score_mate: 3,
      depth: 25,
      seldepth: 30,
      pv: ['a7a6', 'b5a4'],
      nodes: 500000,
      time_ms: 800,
      clock_white_ms: null,
      clock_black_ms: null,
    },
  ],
  created_at: '2025-06-15T10:30:00Z',
  opening_name: 'Ruy Lopez',
  sprt_test_id: null,
  start_fen: null,
  time_control: null,
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('GameReplayPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchGame).mockResolvedValue(sampleGame)
  })

  it('renders the heading', async () => {
    renderAtPath('/games/game-1')
    expect(screen.getByRole('heading', { name: 'Game Replay' })).toBeInTheDocument()
    // Wait for the async fetchGame to settle so state updates land inside act().
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())
  })

  // -----------------------------------------------------------------------
  // No-id route
  // -----------------------------------------------------------------------

  it('shows a message and does not fetch when no id is present', () => {
    renderAtPath('/games')
    expect(screen.getByText('Select a game to view its replay.')).toBeInTheDocument()
    expect(fetchGame).not.toHaveBeenCalled()
  })

  // -----------------------------------------------------------------------
  // Loading
  // -----------------------------------------------------------------------

  it('shows a loading indicator while the game is being fetched', () => {
    vi.mocked(fetchGame).mockReturnValue(new Promise(() => {}))
    renderAtPath('/games/game-1')
    expect(screen.getByText('Loading…')).toBeInTheDocument()
  })

  // -----------------------------------------------------------------------
  // Metadata
  // -----------------------------------------------------------------------

  it('loads the game and displays its metadata under the correct labels', async () => {
    renderAtPath('/games/game-1')
    await waitFor(() => expect(fetchGame).toHaveBeenCalledWith('game-1'))
    await waitFor(() => expect(screen.getByText('Stockfish')).toBeInTheDocument())
    // Assert each value sits directly under its label (dt -> dd), not just
    // that the string appears somewhere on the page — catches a swapped
    // White/Black (or any other mislabelled) field that plain getByText
    // would miss entirely.
    expect(screen.getByText('White').nextElementSibling).toHaveTextContent('Stockfish')
    expect(screen.getByText('Black').nextElementSibling).toHaveTextContent('Leela')
    expect(screen.getByText('Result').nextElementSibling).toHaveTextContent('1-0')
    expect(screen.getByText('Opening').nextElementSibling).toHaveTextContent('Ruy Lopez')
    expect(screen.getByText('Date').nextElementSibling).toHaveTextContent('2025-06-15')
  })

  it('shows a placeholder date instead of a raw fragment for a malformed created_at', async () => {
    vi.mocked(fetchGame).mockResolvedValue({ ...sampleGame, created_at: 'not-a-date' })
    renderAtPath('/games/game-1')
    await waitFor(() =>
      expect(screen.getByText('Date').nextElementSibling).toHaveTextContent('Unknown date'),
    )
  })

  it('falls back to a placeholder when opening_name is null', async () => {
    vi.mocked(fetchGame).mockResolvedValue({ ...sampleGame, opening_name: null })
    renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByText('Unknown opening')).toBeInTheDocument())
  })

  // -----------------------------------------------------------------------
  // 404 / error handling
  // -----------------------------------------------------------------------

  it('shows a friendly message when the game is not found', async () => {
    vi.mocked(fetchGame).mockRejectedValue(new ApiError(404, 'Game not found'))
    renderAtPath('/games/missing')
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Game not found.'))
    expect(screen.queryByTestId('chessboard')).not.toBeInTheDocument()
  })

  it('shows a generic error message for non-404 failures', async () => {
    vi.mocked(fetchGame).mockRejectedValue(new Error('Network error'))
    renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Network error'))
  })

  it('falls back to a generic message when the error has an empty message', async () => {
    // response.statusText is always '' over HTTP/2, and a JSON body with
    // `detail: ""` hits the same path — the alert must never render blank.
    vi.mocked(fetchGame).mockRejectedValue(new ApiError(500, ''))
    renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Failed to load game'))
  })

  // -----------------------------------------------------------------------
  // Start position
  // -----------------------------------------------------------------------

  it('shows the starting position and a neutral eval bar before any navigation', async () => {
    renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())
    expect(screen.getByTestId('chessboard')).toHaveAttribute('data-position', STANDARD_INITIAL_FEN)
    expect(screen.getByText('+0.0')).toBeInTheDocument()
  })

  // -----------------------------------------------------------------------
  // Navigation buttons
  // -----------------------------------------------------------------------

  it('navigates through the game with first/previous/next/last controls', async () => {
    const user = userEvent.setup()
    renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())

    expect(screen.getByRole('button', { name: 'First' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Last' })).toBeEnabled()

    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByTestId('chessboard')).toHaveAttribute(
      'data-position',
      sampleGame.moves[0].fen_after,
    )
    expect(screen.getByText('+0.2')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByTestId('chessboard')).toHaveAttribute(
      'data-position',
      sampleGame.moves[1].fen_after,
    )
    expect(screen.getByText('−0.1')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Previous' }))
    expect(screen.getByTestId('chessboard')).toHaveAttribute(
      'data-position',
      sampleGame.moves[0].fen_after,
    )

    await user.click(screen.getByRole('button', { name: 'Last' }))
    expect(screen.getByTestId('chessboard')).toHaveAttribute(
      'data-position',
      sampleGame.moves[4].fen_after,
    )
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Last' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'First' }))
    expect(screen.getByTestId('chessboard')).toHaveAttribute('data-position', STANDARD_INITIAL_FEN)
  })

  // -----------------------------------------------------------------------
  // MoveList click
  // -----------------------------------------------------------------------

  it('jumps to the clicked move in the move list', async () => {
    const user = userEvent.setup()
    renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())

    // Click via role/name (not getByText, which would still resolve a
    // non-interactive <span>) so this also pins the move as an accessible,
    // keyboard-reachable control.
    await user.click(screen.getByRole('button', { name: /Nf3/ }))

    expect(screen.getByTestId('chessboard')).toHaveAttribute(
      'data-position',
      sampleGame.moves[2].fen_after,
    )
    expect(screen.getByText('+0.3')).toBeInTheDocument()
    expect(screen.getByTestId('pv-line')).toHaveTextContent('b8c6 f1b5 a7a6')
  })

  // -----------------------------------------------------------------------
  // Keyboard navigation
  // -----------------------------------------------------------------------

  it('navigates with arrow keys (left/right step, up/down first/last)', async () => {
    const user = userEvent.setup()
    renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())

    await user.keyboard('{ArrowRight}')
    expect(screen.getByTestId('chessboard')).toHaveAttribute(
      'data-position',
      sampleGame.moves[0].fen_after,
    )

    await user.keyboard('{ArrowRight}')
    expect(screen.getByTestId('chessboard')).toHaveAttribute(
      'data-position',
      sampleGame.moves[1].fen_after,
    )

    await user.keyboard('{ArrowLeft}')
    expect(screen.getByTestId('chessboard')).toHaveAttribute(
      'data-position',
      sampleGame.moves[0].fen_after,
    )

    await user.keyboard('{ArrowDown}')
    expect(screen.getByTestId('chessboard')).toHaveAttribute(
      'data-position',
      sampleGame.moves[4].fen_after,
    )

    await user.keyboard('{ArrowUp}')
    expect(screen.getByTestId('chessboard')).toHaveAttribute('data-position', STANDARD_INITIAL_FEN)
  })

  it('ignores arrow keys while focus is inside a text input', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/games/game-1']}>
        <Routes>
          <Route
            path="games/:id?"
            element={
              <>
                <input aria-label="Unrelated field" />
                <GameReplayPage />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())

    await user.click(screen.getByLabelText('Unrelated field'))
    await user.keyboard('{ArrowRight}')

    expect(screen.getByTestId('chessboard')).toHaveAttribute('data-position', STANDARD_INITIAL_FEN)
  })

  it('ignores arrow keys while focus is inside a select element', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/games/game-1']}>
        <Routes>
          <Route
            path="games/:id?"
            element={
              <>
                <select aria-label="Unrelated select">
                  <option value="a">a</option>
                  <option value="b">b</option>
                </select>
                <GameReplayPage />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())

    await user.click(screen.getByLabelText('Unrelated select'))
    await user.keyboard('{ArrowDown}')

    expect(screen.getByTestId('chessboard')).toHaveAttribute('data-position', STANDARD_INITIAL_FEN)
  })

  it('removes the keydown listener on unmount so a later keypress has no effect', async () => {
    const user = userEvent.setup()
    const { unmount } = renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())

    unmount()
    // No component is mounted to receive this — if the listener leaked,
    // this would throw (or silently mutate unmounted state) instead of
    // being a no-op.
    await user.keyboard('{ArrowRight}')
    expect(screen.queryByTestId('chessboard')).not.toBeInTheDocument()
  })

  // -----------------------------------------------------------------------
  // Board orientation
  // -----------------------------------------------------------------------

  it('defaults board orientation to white and flips on request', async () => {
    const user = userEvent.setup()
    renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())

    expect(screen.getByTestId('chessboard')).toHaveAttribute('data-orientation', 'white')
    expect(screen.getByRole('button', { name: 'Flip Board' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
    expect(screen.getByRole('meter')).toHaveAttribute('data-orientation', 'white')

    await user.click(screen.getByRole('button', { name: 'Flip Board' }))
    expect(screen.getByTestId('chessboard')).toHaveAttribute('data-orientation', 'black')
    // The eval bar's own orientation must track the flip too, or it reads
    // upside-down relative to the board.
    expect(screen.getByRole('button', { name: 'Flip Board' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByRole('meter')).toHaveAttribute('data-orientation', 'black')
  })

  // -----------------------------------------------------------------------
  // Forced mate
  // -----------------------------------------------------------------------

  it('shows a forced-mate score and highlights it on the eval graph', async () => {
    const user = userEvent.setup()
    renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'Last' }))

    expect(screen.getByText('M3')).toBeInTheDocument()
    expect(screen.getByTestId('eval-graph-point-4')).toHaveAttribute('data-current', 'true')
  })

  // -----------------------------------------------------------------------
  // Eval graph data points
  // -----------------------------------------------------------------------

  it('renders one eval graph point per move with a recorded eval, skipping moves with none', async () => {
    renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())

    expect(screen.getByTestId('eval-graph-point-0')).toBeInTheDocument()
    expect(screen.getByTestId('eval-graph-point-1')).toBeInTheDocument()
    expect(screen.getByTestId('eval-graph-point-2')).toBeInTheDocument()
    expect(screen.queryByTestId('eval-graph-point-3')).not.toBeInTheDocument()
    expect(screen.getByTestId('eval-graph-point-4')).toBeInTheDocument()
    expect(screen.getByTestId('eval-graph-line')).toBeInTheDocument()
  })

  // -----------------------------------------------------------------------
  // Zero-move game
  // -----------------------------------------------------------------------

  it('renders a zero-move game without crashing', async () => {
    vi.mocked(fetchGame).mockResolvedValue({ ...sampleGame, moves: [] })
    renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())

    expect(screen.getByText('No moves')).toBeInTheDocument()
    expect(screen.getByText('No evaluation data')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'First' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Last' })).toBeDisabled()
  })

  // -----------------------------------------------------------------------
  // All-null-eval game
  // -----------------------------------------------------------------------

  it('renders a game whose every move has a null eval without crashing', async () => {
    const allNullGame: GameDetail = {
      ...sampleGame,
      moves: sampleGame.moves.map((m) => ({ ...m, score_cp: null, score_mate: null })),
    }
    vi.mocked(fetchGame).mockResolvedValue(allNullGame)
    const user = userEvent.setup()
    renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())

    expect(screen.getByText('No evaluation data')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Next' }))
    // EvalBar has no "no data" state of its own and renders a neutral
    // +0.0 for a missing score; a distinct caption must make clear that
    // this is "no eval recorded", not "the engine evaluated this as
    // equal" — otherwise it silently contradicts EvalGraph's own "No
    // evaluation data" message for the same move.
    expect(screen.getByText('+0.0')).toBeInTheDocument()
    expect(screen.getByTestId('eval-bar-no-data')).toBeInTheDocument()
  })

  // -----------------------------------------------------------------------
  // No recorded eval for a single move
  // -----------------------------------------------------------------------

  it('shows a "no data" caption instead of a fabricated eval for a move with no recorded score', async () => {
    const user = userEvent.setup()
    renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())

    expect(screen.queryByTestId('eval-bar-no-data')).not.toBeInTheDocument()

    // Navigate to move index 3 (Nc6), which carries no recorded eval.
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await user.click(screen.getByRole('button', { name: 'Next' }))

    expect(screen.getByTestId('eval-bar-no-data')).toBeInTheDocument()
    // The eval graph agrees: no data point, but the current-move rule
    // still marks the position.
    expect(screen.queryByTestId('eval-graph-point-3')).not.toBeInTheDocument()
    expect(screen.getByTestId('eval-graph-current-line')).toBeInTheDocument()
  })

  // -----------------------------------------------------------------------
  // Status line
  // -----------------------------------------------------------------------

  it('reports the current move in the status line with full-move notation', async () => {
    const user = userEvent.setup()
    renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())

    expect(screen.getByRole('status')).toHaveTextContent('Starting position')

    await user.click(screen.getByRole('button', { name: 'Last' }))
    // Index 4 (Bb5) is White's 3rd move.
    expect(screen.getByRole('status')).toHaveTextContent('3. Bb5')
    expect(screen.getByRole('status')).toHaveTextContent('move 5 of 5')
  })

  // -----------------------------------------------------------------------
  // Eval sign convention: mover derived from start_fen, not index parity
  // -----------------------------------------------------------------------

  it('derives the mover from a black-to-move start_fen instead of assuming White plays first', async () => {
    const blackToMoveStartFen = 'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 2'
    const blackStartGame: GameDetail = {
      ...sampleGame,
      start_fen: blackToMoveStartFen,
      // moves[0] is now Black's move: raw score_cp is from Black's own
      // point of view (Black is +3.0), so White's perspective must be
      // -3.0 — the opposite of what index-parity (index 0 => "White")
      // would produce.
      moves: [{ ...sampleGame.moves[0], score_cp: 300 }],
    }
    vi.mocked(fetchGame).mockResolvedValue(blackStartGame)
    const user = userEvent.setup()
    renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())

    // The start position itself must render the game's actual start_fen,
    // not silently fall back to the standard initial position.
    expect(screen.getByTestId('chessboard')).toHaveAttribute('data-position', blackToMoveStartFen)

    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('−3.0')).toBeInTheDocument()
    expect(screen.queryByText('+3.0')).not.toBeInTheDocument()
  })

  // -----------------------------------------------------------------------
  // Move numbering: also derived from start_fen, shared with MoveList
  // -----------------------------------------------------------------------

  it('numbers the status line from start_fen when Black moves first', async () => {
    // Position after 1. e4: Black to move, still full move 1.
    const blackToMoveStartFen = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1'
    vi.mocked(fetchGame).mockResolvedValue({ ...sampleGame, start_fen: blackToMoveStartFen })
    const user = userEvent.setup()
    renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())

    // moves[0] is Black's reply, so it is "1...", not "1.".
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByRole('status')).toHaveTextContent('1...')
    expect(screen.getByRole('status')).toHaveTextContent('move 1 of 5')

    // moves[1] is White's, opening full move 2.
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByRole('status')).toHaveTextContent('2.')
  })

  it('numbers the move list from start_fen when Black moves first', async () => {
    const blackToMoveStartFen = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1'
    vi.mocked(fetchGame).mockResolvedValue({ ...sampleGame, start_fen: blackToMoveStartFen })
    renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())

    // 5 half-moves from a Black-to-move start: 1... / 2. / 3. — never "1.".
    expect(screen.getByText('1...')).toBeInTheDocument()
    expect(screen.queryByText('1.')).not.toBeInTheDocument()
    expect(screen.getByText('2.')).toBeInTheDocument()
    expect(screen.getByText('3.')).toBeInTheDocument()
  })

  it('numbers from the start_fen full-move counter, not from 1', async () => {
    // A book line resumed at full move 12, White to move.
    const midGameStartFen = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 12'
    vi.mocked(fetchGame).mockResolvedValue({ ...sampleGame, start_fen: midGameStartFen })
    renderAtPath('/games/game-1')
    await waitFor(() => expect(screen.getByTestId('chessboard')).toBeInTheDocument())

    expect(screen.getByText('12.')).toBeInTheDocument()
    expect(screen.getByText('14.')).toBeInTheDocument()
    expect(screen.queryByText('1.')).not.toBeInTheDocument()
  })
})
