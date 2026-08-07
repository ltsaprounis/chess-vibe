import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { fetchGame, ApiError } from '../services/api'
import type { GameDetail, Move } from '../services/api'
import { Board } from '../components/Board/Board'
import { EvalBar } from '../components/EvalBar/EvalBar'
import { MoveList } from '../components/MoveList/MoveList'
import type { MoveItem } from '../components/MoveList/MoveList'
import { isBlackMoveAt, moveLabel, parseStartPosition } from '../components/MoveList/startPosition'
import type { StartPosition } from '../components/MoveList/startPosition'
import { EvalGraph } from '../components/EvalGraph/EvalGraph'
import type { EvalGraphPoint } from '../components/EvalGraph/EvalGraph'

const STANDARD_INITIAL_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'

/**
 * Current-move indexing convention used throughout this page — applied
 * consistently to the board FEN, EvalBar, PV display, MoveList highlight,
 * and EvalGraph highlight:
 *   -1  → the starting position, before any move has been played.
 *   i   → the position immediately after `game.moves[i]` was played.
 */
const START_POSITION_INDEX = -1

/** Board width (px) — also drives the height of the flanking eval bar and move list columns. */
const BOARD_SIZE_PX = 400

const NAV_BUTTON_CLASS =
  'rounded bg-gray-700 px-3 py-1 text-white hover:bg-gray-600 disabled:cursor-not-allowed disabled:opacity-40'

interface WhitePerspectiveScore {
  scoreCp?: number
  scoreMate?: number
}

/**
 * Eval sign convention: `Move.score_cp` / `Move.score_mate` are stored from
 * the perspective of whichever side made that move (the raw UCI `info
 * score` convention). Moves strictly alternate colour starting from whoever
 * is to move in `start_fen`, so index 0 cannot be assumed to be White's —
 * `start_fen` holds arbitrary opening-book positions, including odd-ply
 * lines with Black to move. The mover therefore comes from
 * {@link isBlackMoveAt} against the parsed `start_fen`, which is the same
 * derivation MoveList uses to number the moves.
 *
 * EvalBar and EvalGraph both read scores as "White's advantage" (the
 * conventional way to read an eval bar/graph), so a move played by Black
 * has its sign flipped here before it reaches either component.
 */
function toWhitePerspective(
  move: Move,
  index: number,
  startPosition: StartPosition,
): WhitePerspectiveScore {
  const isBlackMove = isBlackMoveAt(index, startPosition)
  const scoreCp = move.score_cp === null ? undefined : isBlackMove ? -move.score_cp : move.score_cp
  const scoreMate =
    move.score_mate === null ? undefined : isBlackMove ? -move.score_mate : move.score_mate
  return { scoreCp, scoreMate }
}

/**
 * Matches EvalBar's own formatScore exactly (mate takes priority over cp,
 * same U+2212 minus glyph for negatives) so the same score never reads
 * differently in the move list than it does on the eval bar.
 */
function formatAnnotation(score: WhitePerspectiveScore): string | undefined {
  if (score.scoreMate !== undefined) return `(M${score.scoreMate})`
  if (score.scoreCp !== undefined) {
    const pawns = score.scoreCp / 100
    if (score.scoreCp >= 0) return `(+${pawns.toFixed(1)})`
    return `(−${Math.abs(pawns).toFixed(1)})`
  }
  return undefined
}

/**
 * Renders the date portion of an ISO timestamp. Parses rather than blindly
 * slicing the string, so a malformed/empty `created_at` renders a clear
 * placeholder instead of a truncated fragment of garbage.
 */
function formatGameDate(isoTimestamp: string): string {
  const parsed = new Date(isoTimestamp)
  return Number.isNaN(parsed.getTime()) ? 'Unknown date' : parsed.toISOString().slice(0, 10)
}

/**
 * Route entry point. Delegates to {@link GameReplayView} for a given game
 * id — remounting it (via `key={id}`) whenever the id changes is what
 * resets all of that component's state, so no manual "reset on id change"
 * logic is needed inside an effect.
 */
export function GameReplayPage(): React.JSX.Element {
  const { id } = useParams<{ id?: string }>()

  if (!id) {
    return (
      <main className="p-4">
        <h1 className="mb-4 text-3xl font-bold">Game Replay</h1>
        <p>Select a game to view its replay.</p>
      </main>
    )
  }

  return <GameReplayView key={id} id={id} />
}

interface GameReplayViewProps {
  id: string
}

function GameReplayView({ id }: GameReplayViewProps): React.JSX.Element {
  const [game, setGame] = useState<GameDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [currentMoveIndex, setCurrentMoveIndex] = useState(START_POSITION_INDEX)
  const [orientation, setOrientation] = useState<'white' | 'black'>('white')

  // Fetch the game on mount (a fresh mount per id, via the `key` above).
  // Every setState call below lives inside a promise callback rather than
  // directly in the effect body, so mounting never triggers a synchronous
  // extra render.
  useEffect(() => {
    let cancelled = false

    fetchGame(id)
      .then((data) => {
        if (cancelled) return
        setGame(data)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true)
        } else {
          // `err.message` can legitimately be an empty string (e.g. a
          // response with no `detail` and an HTTP/2 status line, which has
          // no reason phrase) — `||` catches that case as well as
          // non-Error rejections, so the alert is never silently blank.
          setError((err instanceof Error && err.message) || 'Failed to load game')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [id])

  const moves = game?.moves ?? []

  const goFirst = useCallback((): void => setCurrentMoveIndex(START_POSITION_INDEX), [])
  const goLast = useCallback((): void => setCurrentMoveIndex(moves.length - 1), [moves.length])
  const goNext = useCallback(
    (): void => setCurrentMoveIndex((i) => Math.min(i + 1, moves.length - 1)),
    [moves.length],
  )
  const goPrev = useCallback(
    (): void => setCurrentMoveIndex((i) => Math.max(i - 1, START_POSITION_INDEX)),
    [],
  )

  // Arrow-key navigation: Left/Right step one move at a time, Up/Down jump
  // to the very first/last position. Delegates to the same goFirst/goPrev/
  // goNext/goLast callbacks the nav buttons use, so the two input paths
  // can never drift apart. Ignored while the user is typing/selecting in a
  // form field so the shortcuts don't hijack normal text entry or native
  // <select> option changes, and the listener is removed on unmount / when
  // the game changes.
  useEffect(() => {
    if (!game) return

    function isTypingTarget(target: EventTarget | null): boolean {
      if (!(target instanceof HTMLElement)) return false
      return target.closest('input, textarea, select, [contenteditable="true"]') !== null
    }

    function handleKeyDown(e: KeyboardEvent): void {
      if (isTypingTarget(e.target)) return
      switch (e.key) {
        case 'ArrowLeft':
          e.preventDefault()
          goPrev()
          break
        case 'ArrowRight':
          e.preventDefault()
          goNext()
          break
        case 'ArrowUp':
          e.preventDefault()
          goFirst()
          break
        case 'ArrowDown':
          e.preventDefault()
          goLast()
          break
        default:
          break
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [game, goFirst, goPrev, goNext, goLast])

  const boardFen =
    currentMoveIndex === START_POSITION_INDEX
      ? (game?.start_fen ?? STANDARD_INITIAL_FEN)
      : (moves[currentMoveIndex]?.fen_after ?? STANDARD_INITIAL_FEN)

  // Who moves first, and which full move the game resumes at, are read from
  // `start_fen` rather than assumed — an opening-book position can have
  // Black to move and can start well past move 1. This single parse feeds
  // both the eval signs (`toWhitePerspective`) and the move numbering
  // (MoveList, and the status line below).
  const startPosition = parseStartPosition(game?.start_fen)

  const currentScore: WhitePerspectiveScore =
    currentMoveIndex === START_POSITION_INDEX || !moves[currentMoveIndex]
      ? {}
      : toWhitePerspective(moves[currentMoveIndex], currentMoveIndex, startPosition)

  const currentMoveHasEval =
    currentMoveIndex !== START_POSITION_INDEX &&
    (currentScore.scoreCp !== undefined || currentScore.scoreMate !== undefined)

  const currentPv =
    currentMoveIndex === START_POSITION_INDEX ? [] : (moves[currentMoveIndex]?.pv ?? [])

  const moveItems: MoveItem[] = moves.map((m, i) => ({
    san: m.san,
    annotation: formatAnnotation(toWhitePerspective(m, i, startPosition)),
  }))

  const graphPoints: EvalGraphPoint[] = moves.map((m, i) => {
    const score = toWhitePerspective(m, i, startPosition)
    return {
      scoreCp: score.scoreCp ?? null,
      scoreMate: score.scoreMate ?? null,
    }
  })

  const atStart = currentMoveIndex === START_POSITION_INDEX
  const atEnd = currentMoveIndex >= moves.length - 1

  return (
    <main className="p-4">
      <h1 className="mb-4 text-3xl font-bold">Game Replay</h1>

      {loading && <p>Loading…</p>}

      {notFound && (
        <p role="alert" className="rounded bg-red-900/50 p-3 text-red-200">
          Game not found.
        </p>
      )}

      {error && (
        <div role="alert" className="rounded bg-red-900/50 p-3 text-red-200">
          {error}
        </div>
      )}

      {game && (
        <div>
          <dl className="mb-4 grid grid-cols-2 gap-x-6 gap-y-2 text-sm text-gray-300 sm:grid-cols-5">
            <div>
              <dt className="text-xs uppercase text-gray-500">White</dt>
              <dd className="text-white">{game.white_engine}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-gray-500">Black</dt>
              <dd className="text-white">{game.black_engine}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-gray-500">Result</dt>
              <dd className="text-white">{game.result}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-gray-500">Opening</dt>
              <dd className="text-white">{game.opening_name ?? 'Unknown opening'}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-gray-500">Date</dt>
              <dd className="text-white">{formatGameDate(game.created_at)}</dd>
            </div>
          </dl>

          <div className="mb-4 flex flex-wrap items-start gap-4">
            <div className="flex flex-col items-center gap-1">
              <div style={{ height: BOARD_SIZE_PX }}>
                <EvalBar
                  scoreCp={currentScore.scoreCp}
                  scoreMate={currentScore.scoreMate}
                  orientation={orientation}
                />
              </div>
              {/*
               * EvalBar has no "no data" state of its own — a missing
               * score would otherwise silently render as a fabricated
               * neutral +0.0, contradicting EvalGraph's "No evaluation
               * data" message for the exact same move. This caption makes
               * that distinction visible without changing EvalBar's props
               * (other pages render it unconditionally).
               */}
              {currentMoveIndex !== START_POSITION_INDEX && !currentMoveHasEval && (
                <p data-testid="eval-bar-no-data" className="text-[10px] text-gray-500">
                  No eval
                </p>
              )}
            </div>

            <Board position={boardFen} boardOrientation={orientation} boardWidth={BOARD_SIZE_PX} />

            <div className="w-64 overflow-hidden" style={{ height: BOARD_SIZE_PX }}>
              <MoveList
                moves={moveItems}
                currentMoveIndex={currentMoveIndex}
                startPosition={startPosition}
                onMoveClick={setCurrentMoveIndex}
              />
            </div>
          </div>

          <nav aria-label="Move navigation" className="mb-4 flex flex-wrap gap-2">
            <button type="button" onClick={goFirst} disabled={atStart} className={NAV_BUTTON_CLASS}>
              First
            </button>
            <button type="button" onClick={goPrev} disabled={atStart} className={NAV_BUTTON_CLASS}>
              Previous
            </button>
            <button type="button" onClick={goNext} disabled={atEnd} className={NAV_BUTTON_CLASS}>
              Next
            </button>
            <button type="button" onClick={goLast} disabled={atEnd} className={NAV_BUTTON_CLASS}>
              Last
            </button>
            <button
              type="button"
              onClick={() => setOrientation((o) => (o === 'white' ? 'black' : 'white'))}
              aria-pressed={orientation === 'black'}
              className={NAV_BUTTON_CLASS}
            >
              Flip Board
            </button>
          </nav>

          <p role="status" aria-live="polite" className="mb-4 text-sm text-gray-400">
            {atStart
              ? 'Starting position'
              : `${moveLabel(currentMoveIndex, startPosition)} ${moves[currentMoveIndex]?.san ?? ''} — move ${
                  currentMoveIndex + 1
                } of ${moves.length}`}
          </p>

          <div className="mb-4">
            <h2 className="mb-1 text-lg font-semibold">Principal Variation</h2>
            <p data-testid="pv-line" className="font-mono text-sm text-gray-300">
              {currentPv.length > 0 ? currentPv.join(' ') : '—'}
            </p>
          </div>

          <div>
            <h2 className="mb-1 text-lg font-semibold">Evaluation</h2>
            <EvalGraph points={graphPoints} currentMoveIndex={currentMoveIndex} />
          </div>
        </div>
      )}
    </main>
  )
}
