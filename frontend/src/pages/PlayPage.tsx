import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchEngines } from '../services/api'
import type { Engine } from '../services/api'
import { useWebSocket } from '../hooks/useWebSocket'
import { useChessGame } from '../hooks/useChessGame'
import { Board } from '../components/Board/Board'
import { EvalBar } from '../components/EvalBar/EvalBar'
import { MoveList } from '../components/MoveList/MoveList'
import type { MoveItem } from '../components/MoveList/MoveList'
import { PromotionDialog } from '../components/Board/PromotionDialog'
import type { PromotionPiece } from '../components/Board/PromotionDialog'
import type { PieceDropHandlerArgs } from 'react-chessboard'

// ---------------------------------------------------------------------------
// WebSocket message types
// ---------------------------------------------------------------------------

interface WsIncomingMessage {
  type: 'started' | 'engine_move' | 'game_over' | 'error'
  game_id?: string
  fen?: string
  move?: string
  score_cp?: number
  score_mate?: number
  depth?: number
  pv?: string[]
  result?: string
  message?: string
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildWsUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/play`
}

const SELECTED_SQUARE_STYLE: React.CSSProperties = {
  backgroundColor: 'rgba(255, 255, 0, 0.4)',
}

const LEGAL_MOVE_DOT_STYLE: React.CSSProperties = {
  background: 'radial-gradient(circle, rgba(0,0,0,0.2) 25%, transparent 25%)',
}

const CAPTURE_RING_STYLE: React.CSSProperties = {
  background: 'radial-gradient(circle, transparent 60%, rgba(0,0,0,0.2) 60%)',
}

// ---------------------------------------------------------------------------
// PlayPage
// ---------------------------------------------------------------------------

export function PlayPage(): React.JSX.Element {
  // Setup state
  const [engines, setEngines] = useState<Engine[]>([])
  const [selectedEngine, setSelectedEngine] = useState('')
  const [playerColor, setPlayerColor] = useState<'white' | 'black'>('white')
  const [customFen, setCustomFen] = useState('')

  // Game session state
  const [wsUrl, setWsUrl] = useState<string | null>(null)
  const [gameId, setGameId] = useState<string | null>(null)
  const [gameResult, setGameResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [latestEval, setLatestEval] = useState<{ scoreCp?: number; scoreMate?: number }>({})

  // Chess game state
  const { fen, moves, turn, makeMove, addEngineMove, reset, getLegalMoves, isPromotionMove } =
    useChessGame()

  // Selected piece state for click-to-move
  const [selectedSquare, setSelectedSquare] = useState<string | null>(null)

  // Pending promotion state
  const [pendingPromotion, setPendingPromotion] = useState<{ from: string; to: string } | null>(
    null,
  )

  // Ref for start params (sent when WS opens)
  const startParamsRef = useRef<Record<string, unknown> | null>(null)

  // Fetch engines on mount
  useEffect(() => {
    fetchEngines()
      .then((data) => {
        setEngines(data)
        if (data.length > 0) setSelectedEngine(data[0].id)
      })
      .catch(() => setError('Failed to load engines'))
  }, [])

  // WS message handler
  const handleWsMessage = useCallback(
    (data: WsIncomingMessage) => {
      switch (data.type) {
        case 'started':
          setGameId(data.game_id ?? null)
          break
        case 'engine_move':
          if (data.move) {
            addEngineMove(data.move, {
              scoreCp: data.score_cp,
              scoreMate: data.score_mate,
              depth: data.depth,
              pv: data.pv,
            })
          }
          setLatestEval({
            scoreCp: data.score_cp,
            scoreMate: data.score_mate,
          })
          break
        case 'game_over':
          setGameResult(data.result ?? null)
          break
        case 'error':
          setError(data.message ?? 'An error occurred')
          break
      }
    },
    [addEngineMove],
  )

  // WebSocket connection
  const { sendMessage } = useWebSocket<WsIncomingMessage>(wsUrl, {
    onOpen: () => {
      if (startParamsRef.current) {
        sendMessage(startParamsRef.current)
        startParamsRef.current = null
      }
    },
    onMessage: handleWsMessage,
    reconnect: false,
  })

  // Start game handler
  const handleStartGame = (): void => {
    if (!selectedEngine) return
    setError(null)
    setGameResult(null)
    setGameId(null)
    setLatestEval({})
    setSelectedSquare(null)
    setPendingPromotion(null)
    reset(customFen || undefined)

    startParamsRef.current = {
      type: 'start',
      engine_id: selectedEngine,
      player_color: playerColor,
      ...(customFen ? { fen: customFen } : {}),
    }
    setWsUrl(buildWsUrl())
  }

  // New game handler
  const handleNewGame = (): void => {
    setWsUrl(null)
    setGameId(null)
    setGameResult(null)
    setError(null)
    setLatestEval({})
    setSelectedSquare(null)
    setPendingPromotion(null)
    reset()
  }

  // Handle piece drop on the board
  const handlePieceDrop = useCallback(
    ({ sourceSquare, targetSquare }: PieceDropHandlerArgs): boolean => {
      if (!targetSquare) return false

      // Only allow moves on player's turn
      const isPlayerTurn =
        (playerColor === 'white' && turn === 'w') || (playerColor === 'black' && turn === 'b')
      if (!isPlayerTurn) return false

      // Check if this is a promotion move
      if (isPromotionMove(sourceSquare, targetSquare)) {
        setPendingPromotion({ from: sourceSquare, to: targetSquare })
        setSelectedSquare(null)
        return false
      }

      const uci = `${sourceSquare}${targetSquare}`
      if (makeMove(uci)) {
        sendMessage({ type: 'move', move: uci })
        setSelectedSquare(null)
        return true
      }

      return false
    },
    [playerColor, turn, makeMove, sendMessage, isPromotionMove],
  )

  // Handle square click for click-to-move
  const handleSquareClick = useCallback(
    (square: string): void => {
      const isPlayerTurn =
        (playerColor === 'white' && turn === 'w') || (playerColor === 'black' && turn === 'b')
      if (!isPlayerTurn) {
        setSelectedSquare(null)
        return
      }

      // If a piece is selected and the clicked square is a legal target, make the move
      if (selectedSquare) {
        const currentLegalMoves = getLegalMoves(selectedSquare)
        const isLegalTarget = currentLegalMoves.some((m) => m.to === square)

        if (isLegalTarget) {
          // Check if this is a promotion move
          if (isPromotionMove(selectedSquare, square)) {
            setPendingPromotion({ from: selectedSquare, to: square })
            setSelectedSquare(null)
            return
          }

          const uci = `${selectedSquare}${square}`
          if (makeMove(uci)) {
            sendMessage({ type: 'move', move: uci })
            setSelectedSquare(null)
            return
          }
        }
      }

      // If clicking the same square, deselect
      if (selectedSquare === square) {
        setSelectedSquare(null)
        return
      }

      // If clicking a square with a friendly piece, select it
      const pieceMoves = getLegalMoves(square)
      if (pieceMoves.length > 0) {
        setSelectedSquare(square)
      } else {
        setSelectedSquare(null)
      }
    },
    [playerColor, turn, selectedSquare, makeMove, sendMessage, getLegalMoves, isPromotionMove],
  )

  // Handle promotion piece selection
  const handlePromotionSelect = useCallback(
    (piece: PromotionPiece): void => {
      if (!pendingPromotion) return
      const uci = `${pendingPromotion.from}${pendingPromotion.to}${piece}`
      if (makeMove(uci)) {
        sendMessage({ type: 'move', move: uci })
      }
      setPendingPromotion(null)
    },
    [pendingPromotion, makeMove, sendMessage],
  )

  // Handle promotion cancel
  const handlePromotionCancel = useCallback((): void => {
    setPendingPromotion(null)
  }, [])

  // Compute legal moves for the selected square (for highlighting)
  const legalMoves = selectedSquare ? getLegalMoves(selectedSquare) : []
  const captureSquares = new Set(legalMoves.filter((m) => m.isCapture).map((m) => m.to))

  // Build square styles for highlighting
  const squareStyles: Record<string, React.CSSProperties> = {}
  if (selectedSquare) {
    squareStyles[selectedSquare] = SELECTED_SQUARE_STYLE
    for (const move of legalMoves) {
      squareStyles[move.to] = captureSquares.has(move.to)
        ? CAPTURE_RING_STYLE
        : LEGAL_MOVE_DOT_STYLE
    }
  }

  // Escape key deselects the piece
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setSelectedSquare(null)
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  // Convert moves to MoveList format
  const moveItems: MoveItem[] = moves.map((m) => ({
    san: m.san,
    annotation:
      m.scoreCp !== undefined
        ? `(${m.scoreCp >= 0 ? '+' : ''}${(m.scoreCp / 100).toFixed(1)})`
        : m.scoreMate !== undefined
          ? `(M${m.scoreMate})`
          : undefined,
  }))

  const isPlaying = gameId !== null

  return (
    <main className="p-4">
      <h1 className="mb-4 text-3xl font-bold">Play</h1>

      {error && (
        <div role="alert" className="mb-4 rounded bg-red-900/50 p-3 text-red-200">
          {error}
        </div>
      )}

      {gameResult && (
        <div
          role="status"
          className="mb-4 rounded bg-blue-900/50 p-3 text-lg font-semibold text-blue-200"
        >
          Game Over: {gameResult}
        </div>
      )}

      {!isPlaying ? (
        <div className="max-w-md space-y-4">
          {/* Engine selection */}
          <div>
            <label htmlFor="engine-select" className="mb-1 block text-sm font-medium text-gray-300">
              Engine
            </label>
            <select
              id="engine-select"
              value={selectedEngine}
              onChange={(e) => setSelectedEngine(e.target.value)}
              className="w-full rounded border border-gray-600 bg-gray-800 px-3 py-2 text-white"
            >
              {engines.map((engine) => (
                <option key={engine.id} value={engine.id}>
                  {engine.name}
                </option>
              ))}
            </select>
          </div>

          {/* Color selector */}
          <div>
            <span className="mb-1 block text-sm font-medium text-gray-300">Play as</span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setPlayerColor('white')}
                className={`rounded px-4 py-2 ${
                  playerColor === 'white' ? 'bg-white text-black' : 'bg-gray-700 text-gray-300'
                }`}
              >
                White
              </button>
              <button
                type="button"
                onClick={() => setPlayerColor('black')}
                className={`rounded px-4 py-2 ${
                  playerColor === 'black'
                    ? 'bg-gray-900 text-white ring-1 ring-white'
                    : 'bg-gray-700 text-gray-300'
                }`}
              >
                Black
              </button>
            </div>
          </div>

          {/* FEN input */}
          <div>
            <label htmlFor="fen-input" className="mb-1 block text-sm font-medium text-gray-300">
              Starting position (FEN)
            </label>
            <input
              id="fen-input"
              type="text"
              value={customFen}
              onChange={(e) => setCustomFen(e.target.value)}
              placeholder="Leave empty for standard starting position"
              className="w-full rounded border border-gray-600 bg-gray-800 px-3 py-2 text-white placeholder-gray-500"
            />
          </div>

          {/* Start button */}
          <button
            type="button"
            onClick={handleStartGame}
            disabled={!selectedEngine}
            className="w-full rounded bg-green-600 px-4 py-2 font-semibold text-white hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Start Game
          </button>
        </div>
      ) : (
        <div>
          <div className="mb-4 flex items-start gap-4">
            {/* Eval bar */}
            <div className="h-[600px]">
              <EvalBar
                scoreCp={latestEval.scoreCp}
                scoreMate={latestEval.scoreMate}
                orientation={playerColor}
              />
            </div>

            {/* Board */}
            <div className="relative">
              <Board
                position={fen}
                onPieceDrop={handlePieceDrop}
                onSquareClick={handleSquareClick}
                boardOrientation={playerColor}
                squareStyles={squareStyles}
              />
              {pendingPromotion && (
                <PromotionDialog
                  color={playerColor}
                  onSelect={handlePromotionSelect}
                  onCancel={handlePromotionCancel}
                />
              )}
            </div>

            {/* Move list */}
            <div className="h-[600px] w-64 overflow-hidden">
              <MoveList moves={moveItems} currentMoveIndex={moves.length - 1} />
            </div>
          </div>

          <button
            type="button"
            onClick={handleNewGame}
            className="rounded bg-gray-700 px-4 py-2 text-white hover:bg-gray-600"
          >
            New Game
          </button>
        </div>
      )}
    </main>
  )
}
