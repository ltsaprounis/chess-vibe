import { Chessboard } from 'react-chessboard'
import type { PieceDropHandlerArgs, PieceHandlerArgs, SquareHandlerArgs } from 'react-chessboard'

export interface BoardProps {
  position: string
  onPieceDrop?: (args: PieceDropHandlerArgs) => boolean
  onPieceClick?: (args: PieceHandlerArgs) => void
  onSquareClick?: (args: SquareHandlerArgs) => void
  boardOrientation?: 'white' | 'black'
  boardWidth?: number
  arrowsEnabled?: boolean
  squareStyles?: Record<string, React.CSSProperties>
}

export function Board({
  position,
  onPieceDrop,
  onPieceClick,
  onSquareClick,
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
          onPieceClick,
          onSquareClick,
        }}
      />
    </div>
  )
}
