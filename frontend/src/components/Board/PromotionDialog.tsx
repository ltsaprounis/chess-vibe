import { useEffect } from 'react'

export type PromotionPiece = 'q' | 'r' | 'b' | 'n'

export interface PromotionDialogProps {
  color: 'white' | 'black'
  onSelect: (piece: PromotionPiece) => void
  onCancel: () => void
}

const PIECE_LABELS: Record<PromotionPiece, string> = {
  q: 'Queen',
  r: 'Rook',
  b: 'Bishop',
  n: 'Knight',
}

const PIECE_SYMBOLS: Record<'white' | 'black', Record<PromotionPiece, string>> = {
  white: { q: '♕', r: '♖', b: '♗', n: '♘' },
  black: { q: '♛', r: '♜', b: '♝', n: '♞' },
}

const PIECES: PromotionPiece[] = ['q', 'r', 'b', 'n']

export function PromotionDialog({
  color,
  onSelect,
  onCancel,
}: PromotionDialogProps): React.JSX.Element {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onCancel])

  return (
    <div
      data-testid="promotion-backdrop"
      className="absolute inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onCancel}
    >
      <div
        role="dialog"
        aria-label="Choose promotion piece"
        className="flex gap-1 rounded-lg bg-gray-800 p-2 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {PIECES.map((piece) => (
          <button
            key={piece}
            type="button"
            aria-label={PIECE_LABELS[piece]}
            onClick={() => onSelect(piece)}
            className="flex h-16 w-16 items-center justify-center rounded text-5xl transition-colors hover:bg-gray-600"
          >
            {PIECE_SYMBOLS[color][piece]}
          </button>
        ))}
      </div>
    </div>
  )
}
