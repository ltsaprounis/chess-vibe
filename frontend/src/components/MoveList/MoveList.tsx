import { useEffect, useRef } from 'react'
import {
  STANDARD_START_POSITION,
  fullMoveNumberAt,
  isBlackMoveAt,
  type StartPosition,
} from './startPosition'

export interface MoveItem {
  san: string
  annotation?: string
}

export interface MoveListProps {
  moves: MoveItem[]
  currentMoveIndex: number
  /**
   * Position `moves[0]` was played from. Defaults to the standard initial
   * position, which is what a game started from scratch uses.
   */
  startPosition?: StartPosition
  onMoveClick?: (index: number) => void
}

interface MoveCell {
  san: string
  annotation?: string
  index: number
}

interface MoveRow {
  moveNumber: number
  white?: MoveCell
  black?: MoveCell
}

/**
 * Group half-moves into one row per full move. Only the first row can lack a
 * White move — that is the Black-to-move start — since every later full move
 * begins with White by definition.
 */
function groupMoves(moves: MoveItem[], start: StartPosition): MoveRow[] {
  const rows: MoveRow[] = []
  moves.forEach((move, index) => {
    const cell: MoveCell = { san: move.san, annotation: move.annotation, index }
    const moveNumber = fullMoveNumberAt(index, start)
    if (!isBlackMoveAt(index, start)) {
      rows.push({ moveNumber, white: cell })
      return
    }
    const openRow = rows[rows.length - 1]
    if (openRow?.moveNumber === moveNumber) openRow.black = cell
    else rows.push({ moveNumber, black: cell })
  })
  return rows
}

/** Threshold in pixels — auto-scroll only when within this distance of the bottom. */
const SCROLL_THRESHOLD = 50

export function MoveList({
  moves,
  currentMoveIndex,
  startPosition = STANDARD_START_POSITION,
  onMoveClick,
}: MoveListProps): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const prevMovesLengthRef = useRef(moves.length)

  useEffect(() => {
    const container = containerRef.current
    if (!container || moves.length <= prevMovesLengthRef.current) {
      prevMovesLengthRef.current = moves.length
      return
    }
    prevMovesLengthRef.current = moves.length

    const { scrollTop, scrollHeight, clientHeight } = container
    const isNearBottom = scrollHeight - scrollTop - clientHeight <= SCROLL_THRESHOLD
    if (isNearBottom) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [moves.length])

  if (moves.length === 0) {
    return <div className="p-4 text-gray-400">No moves</div>
  }

  const rows = groupMoves(moves, startPosition)

  return (
    <div ref={containerRef} className="h-full overflow-y-auto rounded bg-gray-800 p-2 text-sm">
      {rows.map(({ moveNumber, white, black }) => (
        <div key={moveNumber} className="flex items-baseline gap-1 py-0.5">
          {/*
           * `min-w-8` rather than a fixed width: a leading Black-only row is
           * labelled `12...`, which is wider than the `12.` the column was
           * sized for. Standard games are unaffected — their labels all fit
           * the same 2rem minimum.
           */}
          <span className="min-w-8 shrink-0 whitespace-nowrap text-right text-gray-500">
            {white ? `${moveNumber}.` : `${moveNumber}...`}
          </span>
          {white && (
            <MoveButton
              san={white.san}
              annotation={white.annotation}
              isActive={white.index === currentMoveIndex}
              onClick={() => onMoveClick?.(white.index)}
            />
          )}
          {black && (
            <MoveButton
              san={black.san}
              annotation={black.annotation}
              isActive={black.index === currentMoveIndex}
              onClick={() => onMoveClick?.(black.index)}
            />
          )}
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  )
}

interface MoveButtonProps {
  san: string
  annotation?: string
  isActive: boolean
  onClick: () => void
}

function MoveButton({ san, annotation, isActive, onClick }: MoveButtonProps): React.JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-baseline gap-1 rounded px-1.5 py-0.5 text-left ${
        isActive ? 'bg-blue-600 text-white' : 'text-gray-200 hover:bg-gray-700'
      }`}
    >
      <span>{san}</span>
      {annotation && <span className="text-xs text-gray-400">{annotation}</span>}
    </button>
  )
}
