export type PromotionPiece = 'q' | 'r' | 'b' | 'n'

export interface PromotionDialogProps {
  color: 'white' | 'black'
  onSelect: (piece: PromotionPiece) => void
  onCancel: () => void
}

const PIECES: { piece: PromotionPiece; label: string; white: string; black: string }[] = [
  { piece: 'q', label: 'Queen', white: '♕', black: '♛' },
  { piece: 'r', label: 'Rook', white: '♖', black: '♜' },
  { piece: 'b', label: 'Bishop', white: '♗', black: '♝' },
  { piece: 'n', label: 'Knight', white: '♘', black: '♞' },
]

export function PromotionDialog({
  color,
  onSelect,
  onCancel,
}: PromotionDialogProps): React.JSX.Element {
  return (
    <div
      role="dialog"
      aria-label="Choose promotion piece"
      className="absolute inset-0 z-10 flex items-center justify-center bg-black/60"
      onClick={onCancel}
    >
      <div
        className="flex gap-2 rounded-lg bg-gray-800 p-4 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        {PIECES.map(({ piece, label, white, black }) => (
          <button
            key={piece}
            type="button"
            aria-label={label}
            onClick={() => onSelect(piece)}
            className="flex h-16 w-16 items-center justify-center rounded text-5xl hover:bg-gray-700"
          >
            {color === 'white' ? white : black}
          </button>
        ))}
      </div>
    </div>
  )
}
