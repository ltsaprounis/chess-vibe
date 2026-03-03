import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { PlayPage } from './PlayPage'
import { fetchEngines } from '../services/api'
import { useWebSocket } from '../hooks/useWebSocket'

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('../services/api', () => ({
  fetchEngines: vi.fn(),
}))

vi.mock('../hooks/useWebSocket', () => ({
  useWebSocket: vi.fn(),
}))

let capturedBoardOnPieceDrop:
  | ((args: { piece: string; sourceSquare: string; targetSquare: string | null }) => boolean)
  | undefined

let capturedBoardOnSquareClick: ((args: { piece: unknown; square: string }) => void) | undefined
let capturedSquareStyles: Record<string, React.CSSProperties> | undefined

vi.mock('react-chessboard', () => ({
  Chessboard: ({ options }: { options?: Record<string, unknown> }) => {
    capturedBoardOnPieceDrop = options?.onPieceDrop as typeof capturedBoardOnPieceDrop
    capturedBoardOnSquareClick = options?.onSquareClick as typeof capturedBoardOnSquareClick
    capturedSquareStyles = options?.squareStyles as typeof capturedSquareStyles
    return (
      <div
        data-testid="chessboard"
        data-position={String(options?.position ?? '')}
        data-orientation={String(options?.boardOrientation ?? 'white')}
      />
    )
  },
}))

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const mockEngines = [
  { id: 'engine-1', name: 'Stockfish', dir: '/engines/sf', run: './sf' },
  { id: 'engine-2', name: 'Leela', dir: '/engines/lc', run: './lc' },
]

type WsOnMessage = (data: unknown) => void
type WsOnOpen = () => void

describe('PlayPage', () => {
  let mockSendMessage: ReturnType<typeof vi.fn>
  let capturedOnMessage: WsOnMessage | undefined
  let capturedOnOpen: WsOnOpen | undefined

  beforeEach(() => {
    vi.clearAllMocks()
    mockSendMessage = vi.fn()
    capturedOnMessage = undefined
    capturedOnOpen = undefined
    capturedBoardOnPieceDrop = undefined
    capturedBoardOnSquareClick = undefined
    capturedSquareStyles = undefined

    vi.mocked(fetchEngines).mockResolvedValue(mockEngines)

    vi.mocked(useWebSocket).mockImplementation(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (_url: any, options?: any) => {
        capturedOnMessage = options?.onMessage as WsOnMessage | undefined
        capturedOnOpen = options?.onOpen as WsOnOpen | undefined
        return {
          sendMessage: mockSendMessage as (data: unknown) => void,
          readyState: _url ? WebSocket.OPEN : WebSocket.CLOSED,
          lastMessage: null,
        }
      },
    )
  })

  // -----------------------------------------------------------------------
  // Setup form rendering
  // -----------------------------------------------------------------------

  it('renders the heading', async () => {
    render(<PlayPage />)
    expect(screen.getByRole('heading', { name: 'Play' })).toBeInTheDocument()
    // Wait for async fetchEngines to settle
    await waitFor(() => expect(screen.getByLabelText('Engine')).toBeInTheDocument())
  })

  it('renders setup form with engine dropdown, color selector, and Start Game button', async () => {
    render(<PlayPage />)

    await waitFor(() => {
      expect(screen.getByLabelText('Engine')).toBeInTheDocument()
    })

    expect(screen.getByText('White')).toBeInTheDocument()
    expect(screen.getByText('Black')).toBeInTheDocument()
    expect(screen.getByLabelText(/starting position/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start Game' })).toBeInTheDocument()
  })

  it('populates engine dropdown from fetchEngines', async () => {
    render(<PlayPage />)

    await waitFor(() => {
      expect(screen.getAllByRole('option')).toHaveLength(2)
    })

    const options = screen.getAllByRole('option')
    expect(options[0]).toHaveTextContent('Stockfish')
    expect(options[1]).toHaveTextContent('Leela')
  })

  it('shows error when fetchEngines fails', async () => {
    vi.mocked(fetchEngines).mockRejectedValue(new Error('Network error'))

    render(<PlayPage />)

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Failed to load engines')
    })
  })

  // -----------------------------------------------------------------------
  // Starting a game
  // -----------------------------------------------------------------------

  it('sends correct WS start message on Start Game click', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)

    await waitFor(() => {
      expect(screen.getByLabelText('Engine')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Start Game' }))

    // useWebSocket should have been called with a WS URL
    expect(vi.mocked(useWebSocket)).toHaveBeenCalledWith(
      expect.stringContaining('/ws/play'),
      expect.any(Object),
    )

    // Simulate WS open → start message sent
    act(() => {
      capturedOnOpen?.()
    })

    expect(mockSendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'start',
        engine_id: 'engine-1',
        player_color: 'white',
      }),
    )
  })

  it('renders game area after receiving started message', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)

    await waitFor(() => {
      expect(screen.getByLabelText('Engine')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Start Game' }))

    act(() => {
      capturedOnOpen?.()
    })

    act(() => {
      capturedOnMessage?.({
        type: 'started',
        game_id: 'game-123',
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
      })
    })

    // Setup form gone, game area visible
    expect(screen.queryByRole('button', { name: 'Start Game' })).not.toBeInTheDocument()
    expect(screen.getByTestId('chessboard')).toBeInTheDocument()
    expect(screen.getByRole('meter')).toBeInTheDocument() // EvalBar
  })

  // -----------------------------------------------------------------------
  // Player moves
  // -----------------------------------------------------------------------

  it('sends WS move message when player makes a valid move', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)

    await waitFor(() => {
      expect(screen.getByLabelText('Engine')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Start Game' }))
    act(() => capturedOnOpen?.())
    act(() => {
      capturedOnMessage?.({
        type: 'started',
        game_id: 'game-123',
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
      })
    })

    // Simulate a valid pawn move via onPieceDrop
    let moveResult: boolean | undefined
    act(() => {
      moveResult = capturedBoardOnPieceDrop?.({
        piece: 'wP',
        sourceSquare: 'e2',
        targetSquare: 'e4',
      })
    })

    expect(moveResult).toBe(true)
    expect(mockSendMessage).toHaveBeenCalledWith({ type: 'move', move: 'e2e4' })
  })

  // -----------------------------------------------------------------------
  // Engine moves
  // -----------------------------------------------------------------------

  it('updates board position when engine move is received', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)

    await waitFor(() => {
      expect(screen.getByLabelText('Engine')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Start Game' }))
    act(() => capturedOnOpen?.())
    act(() => {
      capturedOnMessage?.({
        type: 'started',
        game_id: 'game-123',
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
      })
    })

    // Player makes a move first
    act(() => {
      capturedBoardOnPieceDrop?.({ piece: 'wP', sourceSquare: 'e2', targetSquare: 'e4' })
    })

    // Engine responds
    act(() => {
      capturedOnMessage?.({
        type: 'engine_move',
        move: 'e7e5',
        score_cp: -10,
        depth: 20,
        pv: ['e7e5', 'd2d4'],
      })
    })

    // Board position should be updated (after e4, e5)
    const board = screen.getByTestId('chessboard')
    expect(board.getAttribute('data-position')).toContain('4p3')
  })

  // -----------------------------------------------------------------------
  // Game over
  // -----------------------------------------------------------------------

  it('displays game over result', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)

    await waitFor(() => {
      expect(screen.getByLabelText('Engine')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Start Game' }))
    act(() => capturedOnOpen?.())
    act(() => {
      capturedOnMessage?.({
        type: 'started',
        game_id: 'game-123',
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
      })
    })

    act(() => {
      capturedOnMessage?.({
        type: 'game_over',
        result: '1-0',
        game_id: 'game-123',
      })
    })

    expect(screen.getByRole('status')).toHaveTextContent('1-0')
  })

  // -----------------------------------------------------------------------
  // Error handling
  // -----------------------------------------------------------------------

  it('shows error message from WS', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)

    await waitFor(() => {
      expect(screen.getByLabelText('Engine')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Start Game' }))
    act(() => capturedOnOpen?.())
    act(() => {
      capturedOnMessage?.({
        type: 'started',
        game_id: 'game-123',
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
      })
    })

    act(() => {
      capturedOnMessage?.({ type: 'error', message: 'Engine crashed' })
    })

    expect(screen.getByRole('alert')).toHaveTextContent('Engine crashed')
  })

  // -----------------------------------------------------------------------
  // Board orientation
  // -----------------------------------------------------------------------

  it('flips board orientation based on player color', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)

    await waitFor(() => {
      expect(screen.getByLabelText('Engine')).toBeInTheDocument()
    })

    // Select black
    await user.click(screen.getByText('Black'))

    await user.click(screen.getByRole('button', { name: 'Start Game' }))
    act(() => capturedOnOpen?.())
    act(() => {
      capturedOnMessage?.({
        type: 'started',
        game_id: 'game-123',
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
      })
    })

    expect(screen.getByTestId('chessboard').getAttribute('data-orientation')).toBe('black')
  })

  // -----------------------------------------------------------------------
  // New game
  // -----------------------------------------------------------------------

  it('returns to setup form when New Game is clicked', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)

    await waitFor(() => {
      expect(screen.getByLabelText('Engine')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Start Game' }))
    act(() => capturedOnOpen?.())
    act(() => {
      capturedOnMessage?.({
        type: 'started',
        game_id: 'game-123',
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
      })
    })

    // Should be in game mode
    expect(screen.queryByRole('button', { name: 'Start Game' })).not.toBeInTheDocument()

    // Click New Game
    await user.click(screen.getByRole('button', { name: 'New Game' }))

    // Should be back to setup
    expect(screen.getByRole('button', { name: 'Start Game' })).toBeInTheDocument()
  })

  // -----------------------------------------------------------------------
  // No active game rendering
  // -----------------------------------------------------------------------

  it('renders without active game (no chessboard visible)', async () => {
    render(<PlayPage />)
    expect(screen.queryByTestId('chessboard')).not.toBeInTheDocument()
    // Wait for async fetchEngines to settle
    await waitFor(() => expect(screen.getByLabelText('Engine')).toBeInTheDocument())
  })

  // -----------------------------------------------------------------------
  // Legal move indicators
  // -----------------------------------------------------------------------

  async function startGame(user: ReturnType<typeof userEvent.setup>): Promise<void> {
    await waitFor(() => {
      expect(screen.getByLabelText('Engine')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Start Game' }))
    act(() => capturedOnOpen?.())
    act(() => {
      capturedOnMessage?.({
        type: 'started',
        game_id: 'game-123',
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
      })
    })
  }

  it('shows legal move indicators when clicking a piece', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)
    await startGame(user)

    // Click on the e2 pawn (white's turn, player is white)
    act(() => {
      capturedBoardOnSquareClick?.({ piece: null, square: 'e2' })
    })

    // Should have styles for selected square and legal targets
    expect(capturedSquareStyles).toBeDefined()
    expect(capturedSquareStyles?.['e2']).toEqual({ backgroundColor: 'rgba(255, 255, 0, 0.4)' })
    // e2 pawn can move to e3 and e4
    expect(capturedSquareStyles?.['e3']).toBeDefined()
    expect(capturedSquareStyles?.['e4']).toBeDefined()
  })

  it('completes a move when clicking a legal destination square', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)
    await startGame(user)

    // Select the e2 pawn
    act(() => {
      capturedBoardOnSquareClick?.({ piece: null, square: 'e2' })
    })

    // Click on e4 (legal move)
    act(() => {
      capturedBoardOnSquareClick?.({ piece: null, square: 'e4' })
    })

    // Move should have been sent
    expect(mockSendMessage).toHaveBeenCalledWith({ type: 'move', move: 'e2e4' })
    // Selection should be cleared
    expect(capturedSquareStyles).toEqual({})
  })

  it('switches selection when clicking a different friendly piece', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)
    await startGame(user)

    // Select the e2 pawn
    act(() => {
      capturedBoardOnSquareClick?.({ piece: null, square: 'e2' })
    })

    expect(capturedSquareStyles?.['e2']).toBeDefined()

    // Click on d2 pawn instead
    act(() => {
      capturedBoardOnSquareClick?.({ piece: null, square: 'd2' })
    })

    // d2 should now be selected, e2 should no longer have selection style
    expect(capturedSquareStyles?.['d2']).toEqual({ backgroundColor: 'rgba(255, 255, 0, 0.4)' })
    expect(capturedSquareStyles?.['e2']).toBeUndefined()
  })

  it('deselects when clicking an empty non-legal square', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)
    await startGame(user)

    // Select the e2 pawn
    act(() => {
      capturedBoardOnSquareClick?.({ piece: null, square: 'e2' })
    })

    expect(capturedSquareStyles?.['e2']).toBeDefined()

    // Click on a5 (not a legal target for e2 pawn, empty square)
    act(() => {
      capturedBoardOnSquareClick?.({ piece: null, square: 'a5' })
    })

    // Selection should be cleared
    expect(capturedSquareStyles).toEqual({})
  })

  it('does not show indicators on opponent turn', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)
    await startGame(user)

    // Make a move so it's black's turn
    act(() => {
      capturedBoardOnPieceDrop?.({
        piece: 'wP',
        sourceSquare: 'e2',
        targetSquare: 'e4',
      })
    })

    // Now it's black's turn but player is white — clicking should not select
    act(() => {
      capturedBoardOnSquareClick?.({ piece: null, square: 'e7' })
    })

    // No styles should be applied
    expect(capturedSquareStyles).toEqual({})
  })

  it('clears indicators after a piece drop move', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)
    await startGame(user)

    // Select the e2 pawn
    act(() => {
      capturedBoardOnSquareClick?.({ piece: null, square: 'e2' })
    })

    expect(capturedSquareStyles?.['e2']).toBeDefined()

    // Drop a piece instead (drag-and-drop)
    act(() => {
      capturedBoardOnPieceDrop?.({
        piece: 'wP',
        sourceSquare: 'd2',
        targetSquare: 'd4',
      })
    })

    // Selection should be cleared
    expect(capturedSquareStyles).toEqual({})
  })

  it('shows ring style for capture squares', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)
    await startGame(user)

    // Make moves to set up a capture scenario: 1. e4 d5
    act(() => {
      capturedBoardOnPieceDrop?.({
        piece: 'wP',
        sourceSquare: 'e2',
        targetSquare: 'e4',
      })
    })
    act(() => {
      capturedOnMessage?.({
        type: 'engine_move',
        move: 'd7d5',
      })
    })

    // Now select the e4 pawn — it can capture on d5
    act(() => {
      capturedBoardOnSquareClick?.({ piece: null, square: 'e4' })
    })

    // d5 should have capture ring style
    expect(capturedSquareStyles?.['d5']).toEqual({
      background: 'radial-gradient(circle, transparent 60%, rgba(0,0,0,0.2) 60%)',
    })
    // e5 should have dot style (empty square move)
    expect(capturedSquareStyles?.['e5']).toEqual({
      background: 'radial-gradient(circle, rgba(0,0,0,0.2) 25%, transparent 25%)',
    })
  })

  it('deselects piece when Escape is pressed', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)
    await startGame(user)

    // Select the e2 pawn
    act(() => {
      capturedBoardOnSquareClick?.({ piece: null, square: 'e2' })
    })

    expect(capturedSquareStyles?.['e2']).toBeDefined()

    // Press Escape
    await user.keyboard('{Escape}')

    // Selection should be cleared
    expect(capturedSquareStyles).toEqual({})
  })

  // -----------------------------------------------------------------------
  // Pawn promotion
  // -----------------------------------------------------------------------

  // FEN: white pawn on e7 about to promote, black king on a8, white king on e1
  const PROMO_FEN = 'k7/4P3/8/8/8/8/8/4K3 w - - 0 1'

  async function startGameWithFen(
    user: ReturnType<typeof userEvent.setup>,
    fen: string,
  ): Promise<void> {
    await waitFor(() => {
      expect(screen.getByLabelText('Engine')).toBeInTheDocument()
    })

    // Set custom FEN before starting game
    await user.clear(screen.getByLabelText(/starting position/i))
    await user.type(screen.getByLabelText(/starting position/i), fen)

    await user.click(screen.getByRole('button', { name: 'Start Game' }))
    act(() => capturedOnOpen?.())
    act(() => {
      capturedOnMessage?.({
        type: 'started',
        game_id: 'game-promo',
      })
    })
  }

  it('shows promotion dialog when pawn reaches last rank via drag-and-drop', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)
    await startGameWithFen(user, PROMO_FEN)

    // Drag pawn from e7 to e8 — should trigger promotion dialog
    act(() => {
      capturedBoardOnPieceDrop?.({
        piece: 'wP',
        sourceSquare: 'e7',
        targetSquare: 'e8',
      })
    })

    expect(screen.getByRole('dialog', { name: 'Choose promotion piece' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Queen' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rook' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Bishop' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Knight' })).toBeInTheDocument()
  })

  it('completes promotion with selected piece (queen) via drag-and-drop', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)
    await startGameWithFen(user, PROMO_FEN)

    act(() => {
      capturedBoardOnPieceDrop?.({
        piece: 'wP',
        sourceSquare: 'e7',
        targetSquare: 'e8',
      })
    })

    // Select queen
    await user.click(screen.getByRole('button', { name: 'Queen' }))

    expect(mockSendMessage).toHaveBeenCalledWith({ type: 'move', move: 'e7e8q' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('completes promotion with knight via drag-and-drop', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)
    await startGameWithFen(user, PROMO_FEN)

    act(() => {
      capturedBoardOnPieceDrop?.({
        piece: 'wP',
        sourceSquare: 'e7',
        targetSquare: 'e8',
      })
    })

    // Select knight
    await user.click(screen.getByRole('button', { name: 'Knight' }))

    expect(mockSendMessage).toHaveBeenCalledWith({ type: 'move', move: 'e7e8n' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('cancels promotion when backdrop is clicked', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)
    await startGameWithFen(user, PROMO_FEN)

    act(() => {
      capturedBoardOnPieceDrop?.({
        piece: 'wP',
        sourceSquare: 'e7',
        targetSquare: 'e8',
      })
    })

    // Cancel by clicking backdrop
    await user.click(screen.getByTestId('promotion-backdrop'))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    // No move should be sent for the promotion
    expect(mockSendMessage).not.toHaveBeenCalledWith(
      expect.objectContaining({ move: expect.stringContaining('e7e8') }),
    )
  })

  it('shows promotion dialog when pawn reaches last rank via click-to-move', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)
    await startGameWithFen(user, PROMO_FEN)

    // Click-to-move: select pawn on e7, then click e8
    act(() => {
      capturedBoardOnSquareClick?.({ piece: null, square: 'e7' })
    })
    act(() => {
      capturedBoardOnSquareClick?.({ piece: null, square: 'e8' })
    })

    expect(screen.getByRole('dialog', { name: 'Choose promotion piece' })).toBeInTheDocument()
  })

  it('completes promotion with rook via click-to-move', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)
    await startGameWithFen(user, PROMO_FEN)

    act(() => {
      capturedBoardOnSquareClick?.({ piece: null, square: 'e7' })
    })
    act(() => {
      capturedBoardOnSquareClick?.({ piece: null, square: 'e8' })
    })

    await user.click(screen.getByRole('button', { name: 'Rook' }))

    expect(mockSendMessage).toHaveBeenCalledWith({ type: 'move', move: 'e7e8r' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('does not show promotion dialog for engine promotion moves', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)

    await waitFor(() => {
      expect(screen.getByLabelText('Engine')).toBeInTheDocument()
    })

    // Play as black so engine moves as white
    await user.click(screen.getByText('Black'))
    await user.clear(screen.getByLabelText(/starting position/i))
    await user.type(screen.getByLabelText(/starting position/i), PROMO_FEN)
    await user.click(screen.getByRole('button', { name: 'Start Game' }))
    act(() => capturedOnOpen?.())
    act(() => {
      capturedOnMessage?.({
        type: 'started',
        game_id: 'game-engine-promo',
      })
    })

    // Engine (white) promotes the pawn
    act(() => {
      capturedOnMessage?.({
        type: 'engine_move',
        move: 'e7e8q',
      })
    })

    // No promotion dialog should appear for engine moves
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
