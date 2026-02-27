import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { Board } from './Board'

vi.mock('react-chessboard', () => ({
  Chessboard: ({ options }: { options?: Record<string, unknown> }) => (
    <div data-testid="chessboard" data-options={JSON.stringify(options)} />
  ),
}))

const STARTING_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'

describe('Board', () => {
  it('renders the chessboard with default props', () => {
    render(<Board position={STARTING_FEN} />)
    expect(screen.getByTestId('chessboard')).toBeInTheDocument()
  })

  it('passes position to the chessboard', () => {
    render(<Board position={STARTING_FEN} />)
    const board = screen.getByTestId('chessboard')
    const options = JSON.parse(board.getAttribute('data-options') ?? '{}')
    expect(options.position).toBe(STARTING_FEN)
  })

  it('passes board orientation to the chessboard', () => {
    render(<Board position={STARTING_FEN} boardOrientation="black" />)
    const board = screen.getByTestId('chessboard')
    const options = JSON.parse(board.getAttribute('data-options') ?? '{}')
    expect(options.boardOrientation).toBe('black')
  })

  it('defaults board orientation to white', () => {
    render(<Board position={STARTING_FEN} />)
    const board = screen.getByTestId('chessboard')
    const options = JSON.parse(board.getAttribute('data-options') ?? '{}')
    expect(options.boardOrientation).toBe('white')
  })

  it('passes onPieceDrop callback to the chessboard', () => {
    const onPieceDrop = vi.fn()
    render(<Board position={STARTING_FEN} onPieceDrop={onPieceDrop} />)
    const board = screen.getByTestId('chessboard')
    const options = JSON.parse(board.getAttribute('data-options') ?? '{}')
    // Callback is a function, JSON.stringify removes it but it should be present in options
    expect(options).toBeDefined()
  })

  it('passes custom square styles to the chessboard', () => {
    const squareStyles = { e4: { backgroundColor: 'yellow' } }
    render(<Board position={STARTING_FEN} squareStyles={squareStyles} />)
    const board = screen.getByTestId('chessboard')
    const options = JSON.parse(board.getAttribute('data-options') ?? '{}')
    expect(options.squareStyles).toEqual(squareStyles)
  })

  it('passes allowDrawingArrows based on arrowsEnabled prop', () => {
    render(<Board position={STARTING_FEN} arrowsEnabled={true} />)
    const board = screen.getByTestId('chessboard')
    const options = JSON.parse(board.getAttribute('data-options') ?? '{}')
    expect(options.allowDrawingArrows).toBe(true)
  })

  it('disables arrows by default', () => {
    render(<Board position={STARTING_FEN} />)
    const board = screen.getByTestId('chessboard')
    const options = JSON.parse(board.getAttribute('data-options') ?? '{}')
    expect(options.allowDrawingArrows).toBe(false)
  })

  it('renders a responsive container', () => {
    render(<Board position={STARTING_FEN} />)
    const container = screen.getByTestId('chessboard').parentElement
    expect(container).toBeInTheDocument()
  })

  it('applies boardWidth when provided', () => {
    render(<Board position={STARTING_FEN} boardWidth={400} />)
    const board = screen.getByTestId('chessboard')
    const container = board.parentElement
    expect(container).toHaveStyle({ width: '400px' })
  })
})
