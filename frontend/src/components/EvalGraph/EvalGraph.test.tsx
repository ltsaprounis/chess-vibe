import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { EvalGraph } from './EvalGraph'
import type { EvalGraphPoint } from './EvalGraph'

describe('EvalGraph', () => {
  it('renders an empty-state message when there are no points', () => {
    render(<EvalGraph points={[]} currentMoveIndex={-1} />)
    expect(screen.getByText('No evaluation data')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('renders an empty-state message when every point has no recorded eval', () => {
    const points: EvalGraphPoint[] = [
      { scoreCp: null, scoreMate: null },
      { scoreCp: null, scoreMate: null },
    ]
    render(<EvalGraph points={points} currentMoveIndex={-1} />)
    expect(screen.getByText('No evaluation data')).toBeInTheDocument()
  })

  it('renders an accessible chart with a data-bearing label when eval data is present', () => {
    const points: EvalGraphPoint[] = [{ scoreCp: 20, scoreMate: null }]
    render(<EvalGraph points={points} currentMoveIndex={-1} />)
    // The label carries the point count so a wrong/empty data set is
    // detectable non-visually too, not just "some chart exists".
    expect(screen.getByRole('img', { name: /Evaluation graph over 1 moves/ })).toBeInTheDocument()
  })

  it('includes the current move in the accessible label when one is set', () => {
    const points: EvalGraphPoint[] = [
      { scoreCp: 20, scoreMate: null },
      { scoreCp: -10, scoreMate: null },
    ]
    render(<EvalGraph points={points} currentMoveIndex={1} />)
    expect(screen.getByRole('img', { name: /currently showing move 2/ })).toBeInTheDocument()
  })

  it('renders one point per non-null eval and skips null ones', () => {
    const points: EvalGraphPoint[] = [
      { scoreCp: 20, scoreMate: null },
      { scoreCp: null, scoreMate: null },
      { scoreCp: -30, scoreMate: null },
    ]
    render(<EvalGraph points={points} currentMoveIndex={-1} />)
    expect(screen.getByTestId('eval-graph-point-0')).toBeInTheDocument()
    expect(screen.queryByTestId('eval-graph-point-1')).not.toBeInTheDocument()
    expect(screen.getByTestId('eval-graph-point-2')).toBeInTheDocument()
  })

  it('does not render a connecting line with fewer than two valid points', () => {
    const points: EvalGraphPoint[] = [
      { scoreCp: 20, scoreMate: null },
      { scoreCp: null, scoreMate: null },
    ]
    render(<EvalGraph points={points} currentMoveIndex={-1} />)
    expect(screen.queryByTestId('eval-graph-line')).not.toBeInTheDocument()
  })

  it('renders a connecting line with two or more valid points', () => {
    const points: EvalGraphPoint[] = [
      { scoreCp: 20, scoreMate: null },
      { scoreCp: -30, scoreMate: null },
    ]
    render(<EvalGraph points={points} currentMoveIndex={-1} />)
    expect(screen.getByTestId('eval-graph-line')).toBeInTheDocument()
  })

  it('marks the current move point with data-current and a visible highlight', () => {
    const points: EvalGraphPoint[] = [
      { scoreCp: 20, scoreMate: null },
      { scoreCp: -30, scoreMate: null },
    ]
    render(<EvalGraph points={points} currentMoveIndex={1} />)
    const current = screen.getByTestId('eval-graph-point-1')
    const other = screen.getByTestId('eval-graph-point-0')
    expect(other).toHaveAttribute('data-current', 'false')
    expect(current).toHaveAttribute('data-current', 'true')
    // Assert the actual visible cue, not just the test-only attribute —
    // deleting the highlight styling entirely would still leave
    // data-current="true" set and this test green otherwise.
    expect(current).toHaveClass('fill-yellow-400')
    expect(current).toHaveAttribute('r', '4')
    expect(other).not.toHaveClass('fill-yellow-400')
    expect(other).toHaveAttribute('r', '2')
  })

  it('marks no point as current when currentMoveIndex is -1 (start position)', () => {
    const points: EvalGraphPoint[] = [{ scoreCp: 20, scoreMate: null }]
    render(<EvalGraph points={points} currentMoveIndex={-1} />)
    expect(screen.getByTestId('eval-graph-point-0')).toHaveAttribute('data-current', 'false')
    expect(screen.queryByTestId('eval-graph-current-line')).not.toBeInTheDocument()
  })

  it('draws a current-move rule even when the current move has no recorded eval', () => {
    const points: EvalGraphPoint[] = [
      { scoreCp: 20, scoreMate: null },
      { scoreCp: null, scoreMate: null },
      { scoreCp: -30, scoreMate: null },
    ]
    render(<EvalGraph points={points} currentMoveIndex={1} />)
    // Index 1 has no eval, so it contributes no circle...
    expect(screen.queryByTestId('eval-graph-point-1')).not.toBeInTheDocument()
    // ...but the current-move indicator must still mark that position.
    expect(screen.getByTestId('eval-graph-current-line')).toBeInTheDocument()
  })

  it('clamps a forced mate to the top of the chart for White and the bottom for Black', () => {
    const points: EvalGraphPoint[] = [
      { scoreCp: null, scoreMate: 4 }, // White mates
      { scoreCp: null, scoreMate: -4 }, // Black mates
    ]
    render(<EvalGraph points={points} currentMoveIndex={-1} />)
    const whiteMatePoint = screen.getByTestId('eval-graph-point-0')
    const blackMatePoint = screen.getByTestId('eval-graph-point-1')
    const whiteY = Number(whiteMatePoint.getAttribute('cy'))
    const blackY = Number(blackMatePoint.getAttribute('cy'))
    // Lower cy = higher on the chart. White's mate (good for White) should
    // plot above (smaller y) Black's mate (bad for White).
    expect(whiteY).toBeLessThan(blackY)
  })

  it('clamps very large centipawn scores to the same edge as a forced mate', () => {
    const points: EvalGraphPoint[] = [
      { scoreCp: 5000, scoreMate: null },
      { scoreCp: null, scoreMate: 1 },
    ]
    render(<EvalGraph points={points} currentMoveIndex={-1} />)
    const outlierY = screen.getByTestId('eval-graph-point-0').getAttribute('cy')
    const mateY = screen.getByTestId('eval-graph-point-1').getAttribute('cy')
    expect(outlierY).toBe(mateY)
  })

  it('treats scoreMate 0 as Black-favoured, matching EvalBar own > 0 boundary', () => {
    // EvalBar.computeWhitePercent uses `scoreMate > 0 ? 100 : 0`, i.e. a
    // mate of exactly 0 reads as bad for White (0%, bottom of the bar).
    // The graph must agree, not use a `>= 0` test that would plot it at
    // the top instead.
    const points: EvalGraphPoint[] = [
      { scoreCp: null, scoreMate: 0 },
      { scoreCp: null, scoreMate: 4 }, // clearly White-favoured, for comparison
    ]
    render(<EvalGraph points={points} currentMoveIndex={-1} />)
    const mateZeroY = Number(screen.getByTestId('eval-graph-point-0').getAttribute('cy'))
    const whiteMateY = Number(screen.getByTestId('eval-graph-point-1').getAttribute('cy'))
    expect(mateZeroY).toBeGreaterThan(whiteMateY)
  })

  it('draws a dashed zero reference line', () => {
    const points: EvalGraphPoint[] = [{ scoreCp: 20, scoreMate: null }]
    const { container } = render(<EvalGraph points={points} currentMoveIndex={-1} />)
    const zeroLine = container.querySelector('line')
    expect(zeroLine).toBeInTheDocument()
  })

  it('pins the exact x/y geometry for a known fixture, including a gap at a null-eval index', () => {
    // points[1] is null and must leave a gap (point 2 stays at its own
    // x-position, x is not compacted to slot 1) rather than being
    // silently dropped from the index space.
    const points: EvalGraphPoint[] = [
      { scoreCp: 500, scoreMate: null }, // index 0: max clamp, cy at the very top
      { scoreCp: null, scoreMate: null }, // index 1: no point
      { scoreCp: -500, scoreMate: null }, // index 2: max negative clamp, cy at the very bottom
    ]
    render(<EvalGraph points={points} currentMoveIndex={-1} />)

    // VIEW_WIDTH=600, PADDING=10 => innerWidth=580; lastIndex = 2.
    // xAt(0) = 10 + (0/2)*580 = 10; xAt(2) = 10 + (2/2)*580 = 590.
    expect(screen.getByTestId('eval-graph-point-0')).toHaveAttribute('cx', '10')
    expect(screen.getByTestId('eval-graph-point-2')).toHaveAttribute('cx', '590')
    expect(screen.queryByTestId('eval-graph-point-1')).not.toBeInTheDocument()

    // A positive cp must plot above (smaller cy than) a negative cp.
    const topY = Number(screen.getByTestId('eval-graph-point-0').getAttribute('cy'))
    const bottomY = Number(screen.getByTestId('eval-graph-point-2').getAttribute('cy'))
    expect(topY).toBeLessThan(bottomY)

    // The connecting line jumps straight from index 0 to index 2 (a gap
    // at the null index), not to a compacted x for the second point.
    expect(screen.getByTestId('eval-graph-line')).toHaveAttribute(
      'd',
      `M 10 ${topY} L 590 ${bottomY}`,
    )
  })

  it('shrinks point radius once the game is long enough for dots to overlap', () => {
    const many: EvalGraphPoint[] = Array.from({ length: 200 }, (_, i) => ({
      scoreCp: i % 2 === 0 ? 10 : -10,
      scoreMate: null,
    }))
    render(<EvalGraph points={many} currentMoveIndex={-1} />)
    expect(screen.getByTestId('eval-graph-point-0')).toHaveAttribute('r', '1.5')
  })
})
