import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { MoveList } from './MoveList'
import type { MoveItem } from './MoveList'
import { STANDARD_START_POSITION } from './startPosition'
import type { StartPosition } from './startPosition'

/** Generate a list of N dummy half-moves. */
function makeMoves(count: number): MoveItem[] {
  return Array.from({ length: count }, (_, i) => ({ san: `m${i}` }))
}

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

  it('renders a scrollable container with h-full', () => {
    const { container } = render(<MoveList moves={sampleMoves} currentMoveIndex={0} />)
    const scrollContainer = container.querySelector('.overflow-y-auto')
    expect(scrollContainer).toBeInTheDocument()
    expect(scrollContainer).toHaveClass('h-full')
  })

  it('auto-scrolls when a new move is added and user is near the bottom', () => {
    const scrollIntoViewSpy = vi.spyOn(Element.prototype, 'scrollIntoView')

    const moves = makeMoves(4)
    const { rerender } = render(<MoveList moves={moves} currentMoveIndex={3} />)

    // Simulate user being near the bottom (default jsdom scroll values are 0)
    const newMoves = makeMoves(5)
    rerender(<MoveList moves={newMoves} currentMoveIndex={4} />)

    expect(scrollIntoViewSpy).toHaveBeenCalledWith({ behavior: 'smooth', block: 'nearest' })
    scrollIntoViewSpy.mockRestore()
  })

  it('does not auto-scroll when moves are reset to fewer moves', () => {
    const scrollIntoViewSpy = vi.spyOn(Element.prototype, 'scrollIntoView')

    const moves = makeMoves(10)
    const { rerender } = render(<MoveList moves={moves} currentMoveIndex={9} />)

    scrollIntoViewSpy.mockClear()

    // Fewer moves (e.g. new game reset)
    const fewerMoves = makeMoves(2)
    rerender(<MoveList moves={fewerMoves} currentMoveIndex={1} />)

    expect(scrollIntoViewSpy).not.toHaveBeenCalled()
    scrollIntoViewSpy.mockRestore()
  })

  // -------------------------------------------------------------------------
  // Non-standard starting positions (opening-book FENs)
  // -------------------------------------------------------------------------

  describe('with a Black-to-move starting position', () => {
    // The position after 1. e4 — Black to move, still full-move 1 (a FEN's
    // full-move counter increments only after Black has played). The game's
    // recorded moves therefore begin with Black's reply.
    const blackToMove: StartPosition = { blackToMove: true, fullMoveNumber: 1 }

    // The Ruy Lopez continued from that position: the recorded moves are
    // Black's e5, then White's Nf3, Black's Nc6, White's Bb5. Correct
    // rendering is `1... e5`, `2. Nf3 Nc6`, `3. Bb5`.
    const blackFirstMoves: MoveItem[] = [
      { san: 'e5' },
      { san: 'Nf3' },
      { san: 'Nc6' },
      { san: 'Bb5' },
    ]

    it('numbers the first Black move as "1..." rather than "1."', () => {
      render(<MoveList moves={blackFirstMoves} currentMoveIndex={-1} startPosition={blackToMove} />)

      expect(screen.getByText('1...')).toBeInTheDocument()
      expect(screen.queryByText('1.')).not.toBeInTheDocument()
    })

    it('pairs every following move onto the correct full move', () => {
      render(<MoveList moves={blackFirstMoves} currentMoveIndex={-1} startPosition={blackToMove} />)

      expect(screen.getByText('2.')).toBeInTheDocument()
      expect(screen.getByText('3.')).toBeInTheDocument()
      // Three rows, exactly one of which is the leading Black-only row.
      expect(screen.getAllByText(/^\d+\.$/)).toHaveLength(2)
      expect(screen.getAllByText(/^\d+\.\.\.$/)).toHaveLength(1)
    })

    it('renders the leading Black move alone on its own row', () => {
      render(<MoveList moves={blackFirstMoves} currentMoveIndex={-1} startPosition={blackToMove} />)

      const firstRow = screen.getByText('1...').closest('div')
      expect(firstRow).not.toBeNull()
      const rowButtons = firstRow?.querySelectorAll('button') ?? []
      expect(rowButtons).toHaveLength(1)
      expect(rowButtons[0]).toHaveTextContent('e5')

      // ...and the next row carries White's move followed by Black's.
      const secondRow = screen.getByText('2.').closest('div')
      const secondRowButtons = secondRow?.querySelectorAll('button') ?? []
      expect(secondRowButtons).toHaveLength(2)
      expect(secondRowButtons[0]).toHaveTextContent('Nf3')
      expect(secondRowButtons[1]).toHaveTextContent('Nc6')
    })

    it('still highlights the move at currentMoveIndex', () => {
      render(<MoveList moves={blackFirstMoves} currentMoveIndex={0} startPosition={blackToMove} />)

      expect(screen.getByText('e5').closest('button')).toHaveClass('bg-blue-600')
      expect(screen.getByText('Nf3').closest('button')).not.toHaveClass('bg-blue-600')
    })

    it('reports unshifted move indices to onMoveClick', async () => {
      const user = userEvent.setup()
      const onMoveClick = vi.fn()
      render(
        <MoveList
          moves={blackFirstMoves}
          currentMoveIndex={-1}
          startPosition={blackToMove}
          onMoveClick={onMoveClick}
        />,
      )

      await user.click(screen.getByText('e5'))
      expect(onMoveClick).toHaveBeenCalledWith(0)

      await user.click(screen.getByText('Nf3'))
      expect(onMoveClick).toHaveBeenCalledWith(1)
    })
  })

  describe('with a starting full-move number other than 1', () => {
    it('numbers from the starting full move for a White-to-move start', () => {
      render(
        <MoveList
          moves={sampleMoves}
          currentMoveIndex={-1}
          startPosition={{ blackToMove: false, fullMoveNumber: 12 }}
        />,
      )

      expect(screen.getByText('12.')).toBeInTheDocument()
      expect(screen.getByText('13.')).toBeInTheDocument()
      expect(screen.getByText('14.')).toBeInTheDocument()
      expect(screen.queryByText('1.')).not.toBeInTheDocument()
    })

    it('numbers from the starting full move for a Black-to-move start', () => {
      render(
        <MoveList
          moves={sampleMoves}
          currentMoveIndex={-1}
          startPosition={{ blackToMove: true, fullMoveNumber: 12 }}
        />,
      )

      expect(screen.getByText('12...')).toBeInTheDocument()
      expect(screen.getByText('13.')).toBeInTheDocument()
      expect(screen.getByText('14.')).toBeInTheDocument()
    })
  })

  it('defaults to the standard start when no startPosition is given', () => {
    const { unmount } = render(<MoveList moves={sampleMoves} currentMoveIndex={-1} />)
    const implicit = screen.getAllByText(/^\d+\.$/).map((el) => el.textContent)
    unmount()

    render(
      <MoveList
        moves={sampleMoves}
        currentMoveIndex={-1}
        startPosition={STANDARD_START_POSITION}
      />,
    )
    expect(screen.getAllByText(/^\d+\.$/).map((el) => el.textContent)).toEqual(implicit)
  })
})
