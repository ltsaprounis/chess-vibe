import { Chessboard } from 'react-chessboard'
import type { PieceDropHandlerArgs } from 'react-chessboard'

export interface BoardProps {
  position: string
  onPieceDrop?: (args: PieceDropHandlerArgs) => boolean
  boardOrientation?: 'white' | 'black'
  boardWidth?: number
  arrowsEnabled?: boolean
  squareStyles?: Record<string, React.CSSProperties>
}

export function Board({
  position,
  onPieceDrop,
  boardOrientation = 'white',
  boardWidth,
  arrowsEnabled = false,
  squareStyles,
}: BoardProps): React.JSX.Element {
  return (
    <div
      className="aspect-square w-full max-w-[600px]"
      style={boardWidth ? { width: `${boardWidth}px` } : undefined}
    >
      <Chessboard
        options={{
          position,
          boardOrientation,
          allowDrawingArrows: arrowsEnabled,
          squareStyles,
          onPieceDrop,
        }}
      />
    </div>
  )
}
