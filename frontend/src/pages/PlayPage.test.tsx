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

let capturedBoardOnPieceClick:
  | ((args: { isSparePiece: boolean; piece: { pieceType: string }; square: string }) => void)
  | undefined

let capturedBoardSquareStyles: Record<string, React.CSSProperties> | undefined

vi.mock('react-chessboard', () => ({
  Chessboard: ({ options }: { options?: Record<string, unknown> }) => {
    capturedBoardOnPieceDrop = options?.onPieceDrop as typeof capturedBoardOnPieceDrop
    capturedBoardOnPieceClick = options?.onPieceClick as typeof capturedBoardOnPieceClick
    capturedBoardSquareStyles = options?.squareStyles as typeof capturedBoardSquareStyles
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
    capturedBoardOnPieceClick = undefined
    capturedBoardSquareStyles = undefined

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
  // Pawn promotion
  // -----------------------------------------------------------------------

  it('shows promotion dialog when pawn reaches last rank', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)

    await waitFor(() => {
      expect(screen.getByLabelText('Engine')).toBeInTheDocument()
    })

    // Set a promotion FEN via the FEN input
    const fenInput = screen.getByLabelText(/starting position/i)
    await user.clear(fenInput)
    await user.type(fenInput, '8/4P3/8/8/8/8/8/4K2k w - - 0 1')

    await user.click(screen.getByRole('button', { name: 'Start Game' }))
    act(() => capturedOnOpen?.())
    act(() => {
      capturedOnMessage?.({
        type: 'started',
        game_id: 'game-promo',
      })
    })

    // Attempt to promote the pawn
    act(() => {
      capturedBoardOnPieceDrop?.({
        piece: 'wP',
        sourceSquare: 'e7',
        targetSquare: 'e8',
      })
    })

    // Promotion dialog should appear
    expect(screen.getByRole('dialog', { name: /promotion/i })).toBeInTheDocument()
  })

  it('sends promotion move with selected piece', async () => {
    const user = userEvent.setup()
    render(<PlayPage />)

    await waitFor(() => {
      expect(screen.getByLabelText('Engine')).toBeInTheDocument()
    })

    const fenInput = screen.getByLabelText(/starting position/i)
    await user.clear(fenInput)
    await user.type(fenInput, '8/4P3/8/8/8/8/8/4K2k w - - 0 1')

    await user.click(screen.getByRole('button', { name: 'Start Game' }))
    act(() => capturedOnOpen?.())
    act(() => {
      capturedOnMessage?.({
        type: 'started',
        game_id: 'game-promo',
      })
    })

    // Trigger promotion
    act(() => {
      capturedBoardOnPieceDrop?.({
        piece: 'wP',
        sourceSquare: 'e7',
        targetSquare: 'e8',
      })
    })

    // Select knight promotion
    await user.click(screen.getByRole('button', { name: 'Knight' }))

    // Should send the knight promotion move
    expect(mockSendMessage).toHaveBeenCalledWith({ type: 'move', move: 'e7e8n' })
    // Dialog should close
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  // -----------------------------------------------------------------------
  // Legal move highlighting
  // -----------------------------------------------------------------------

  it('highlights legal moves when a piece is clicked', async () => {
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
        game_id: 'game-highlight',
      })
    })

    // Click on the e2 pawn
    act(() => {
      capturedBoardOnPieceClick?.({
        isSparePiece: false,
        piece: { pieceType: 'P' },
        square: 'e2',
      })
    })

    // squareStyles should contain the selected square and legal move targets
    expect(capturedBoardSquareStyles).toBeDefined()
    expect(capturedBoardSquareStyles!['e2']).toBeDefined()
    expect(capturedBoardSquareStyles!['e3']).toBeDefined()
    expect(capturedBoardSquareStyles!['e4']).toBeDefined()
  })
})
