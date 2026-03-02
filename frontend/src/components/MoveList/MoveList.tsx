import { useEffect, useRef } from 'react'

export interface MoveItem {
  san: string
  annotation?: string
}

export interface MoveListProps {
  moves: MoveItem[]
  currentMoveIndex: number
  onMoveClick?: (index: number) => void
}

interface MovePair {
  moveNumber: number
  white: { san: string; annotation?: string; index: number }
  black?: { san: string; annotation?: string; index: number }
}

function groupMoves(moves: MoveItem[]): MovePair[] {
  const pairs: MovePair[] = []
  for (let i = 0; i < moves.length; i += 2) {
    const white = moves[i]
    const black = moves[i + 1]
    pairs.push({
      moveNumber: Math.floor(i / 2) + 1,
      white: { san: white.san, annotation: white.annotation, index: i },
      black: black ? { san: black.san, annotation: black.annotation, index: i + 1 } : undefined,
    })
  }
  return pairs
}

/** Threshold in pixels — auto-scroll only when within this distance of the bottom. */
const SCROLL_THRESHOLD = 50

export function MoveList({
  moves,
  currentMoveIndex,
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

  const pairs = groupMoves(moves)

  return (
    <div ref={containerRef} className="h-full overflow-y-auto rounded bg-gray-800 p-2 text-sm">
      {pairs.map((pair) => (
        <div key={pair.moveNumber} className="flex items-baseline gap-1 py-0.5">
          <span className="w-8 shrink-0 text-right text-gray-500">{pair.moveNumber}.</span>
          <MoveButton
            san={pair.white.san}
            annotation={pair.white.annotation}
            isActive={pair.white.index === currentMoveIndex}
            onClick={() => onMoveClick?.(pair.white.index)}
          />
          {pair.black && (
            <MoveButton
              san={pair.black.san}
              annotation={pair.black.annotation}
              isActive={pair.black.index === currentMoveIndex}
              onClick={() => onMoveClick?.(pair.black.index)}
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
