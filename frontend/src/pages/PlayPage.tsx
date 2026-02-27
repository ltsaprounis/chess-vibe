import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchEngines } from '../services/api'
import type { Engine } from '../services/api'
import { useWebSocket } from '../hooks/useWebSocket'
import { useChessGame } from '../hooks/useChessGame'
import { Board } from '../components/Board/Board'
import { EvalBar } from '../components/EvalBar/EvalBar'
import { MoveList } from '../components/MoveList/MoveList'
import type { MoveItem } from '../components/MoveList/MoveList'
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
  const { fen, moves, turn, makeMove, addEngineMove, reset } = useChessGame()

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

      const uci = `${sourceSquare}${targetSquare}`
      if (makeMove(uci)) {
        sendMessage({ type: 'move', move: uci })
        return true
      }

      // Try queen promotion
      const promoUci = `${uci}q`
      if (makeMove(promoUci)) {
        sendMessage({ type: 'move', move: promoUci })
        return true
      }

      return false
    },
    [playerColor, turn, makeMove, sendMessage],
  )

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
            <Board position={fen} onPieceDrop={handlePieceDrop} boardOrientation={playerColor} />

            {/* Move list */}
            <div className="h-[600px] w-64">
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
