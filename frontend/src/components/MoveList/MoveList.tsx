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

export function MoveList({
  moves,
  currentMoveIndex,
  onMoveClick,
}: MoveListProps): React.JSX.Element {
  if (moves.length === 0) {
    return <div className="p-4 text-gray-400">No moves</div>
  }

  const pairs = groupMoves(moves)

  return (
    <div className="h-full overflow-y-auto rounded bg-gray-800 p-2 text-sm">
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
