import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { MoveList } from './MoveList'
import type { MoveItem } from './MoveList'

describe('MoveList', () => {
  const sampleMoves: MoveItem[] = [
    { san: 'e4' },
    { san: 'e5' },
    { san: 'Nf3', annotation: '+0.3' },
    { san: 'Nc6', annotation: '+0.1' },
    { san: 'Bb5' },
  ]

  it('renders an empty state when no moves are provided', () => {
    render(<MoveList moves={[]} currentMoveIndex={-1} />)
    expect(screen.getByText('No moves')).toBeInTheDocument()
  })

  it('renders move numbers correctly', () => {
    render(<MoveList moves={sampleMoves} currentMoveIndex={-1} />)
    expect(screen.getByText('1.')).toBeInTheDocument()
    expect(screen.getByText('2.')).toBeInTheDocument()
    expect(screen.getByText('3.')).toBeInTheDocument()
  })

  it('renders move SAN notation', () => {
    render(<MoveList moves={sampleMoves} currentMoveIndex={-1} />)
    expect(screen.getByText('e4')).toBeInTheDocument()
    expect(screen.getByText('e5')).toBeInTheDocument()
    expect(screen.getByText('Nf3')).toBeInTheDocument()
    expect(screen.getByText('Nc6')).toBeInTheDocument()
    expect(screen.getByText('Bb5')).toBeInTheDocument()
  })

  it('displays eval annotations when provided', () => {
    render(<MoveList moves={sampleMoves} currentMoveIndex={-1} />)
    expect(screen.getByText('+0.3')).toBeInTheDocument()
    expect(screen.getByText('+0.1')).toBeInTheDocument()
  })

  it('highlights the current move', () => {
    render(<MoveList moves={sampleMoves} currentMoveIndex={2} />)
    const moveButton = screen.getByText('Nf3').closest('button')
    expect(moveButton).toHaveClass('bg-blue-600')
  })

  it('does not highlight non-current moves', () => {
    render(<MoveList moves={sampleMoves} currentMoveIndex={2} />)
    const moveButton = screen.getByText('e4').closest('button')
    expect(moveButton).not.toHaveClass('bg-blue-600')
  })

  it('calls onMoveClick with the correct index when a move is clicked', async () => {
    const user = userEvent.setup()
    const onMoveClick = vi.fn()
    render(<MoveList moves={sampleMoves} currentMoveIndex={-1} onMoveClick={onMoveClick} />)

    await user.click(screen.getByText('Nf3'))
    expect(onMoveClick).toHaveBeenCalledWith(2)
  })

  it('calls onMoveClick with index 0 for the first move', async () => {
    const user = userEvent.setup()
    const onMoveClick = vi.fn()
    render(<MoveList moves={sampleMoves} currentMoveIndex={-1} onMoveClick={onMoveClick} />)

    await user.click(screen.getByText('e4'))
    expect(onMoveClick).toHaveBeenCalledWith(0)
  })

  it('renders without onMoveClick callback', () => {
    render(<MoveList moves={sampleMoves} currentMoveIndex={0} />)
    expect(screen.getByText('e4')).toBeInTheDocument()
  })

  it('handles a single move (white only, no black)', () => {
    render(<MoveList moves={[{ san: 'e4' }]} currentMoveIndex={0} />)
    expect(screen.getByText('1.')).toBeInTheDocument()
    expect(screen.getByText('e4')).toBeInTheDocument()
  })

  it('handles an odd number of moves (last row has only white move)', () => {
    render(<MoveList moves={sampleMoves} currentMoveIndex={-1} />)
    // 5 moves = 3 rows, last row has only white move (Bb5)
    expect(screen.getByText('3.')).toBeInTheDocument()
    expect(screen.getByText('Bb5')).toBeInTheDocument()
  })

  it('groups moves into pairs for display', () => {
    render(<MoveList moves={sampleMoves} currentMoveIndex={-1} />)
    // Verify all 3 move numbers present for 5 moves
    const moveNumbers = screen.getAllByText(/^\d+\.$/)
    expect(moveNumbers).toHaveLength(3)
  })
})
