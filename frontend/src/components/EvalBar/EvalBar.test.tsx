import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { EvalBar } from './EvalBar'

describe('EvalBar', () => {
  it('renders the eval bar', () => {
    render(<EvalBar />)
    expect(screen.getByRole('meter')).toBeInTheDocument()
  })

  it('displays "+0.0" when no score is provided', () => {
    render(<EvalBar />)
    expect(screen.getByText('+0.0')).toBeInTheDocument()
  })

  it('displays positive centipawn score', () => {
    render(<EvalBar scoreCp={150} />)
    expect(screen.getByText('+1.5')).toBeInTheDocument()
  })

  it('displays negative centipawn score', () => {
    render(<EvalBar scoreCp={-200} />)
    expect(screen.getByText('−2.0')).toBeInTheDocument()
  })

  it('displays zero centipawn score', () => {
    render(<EvalBar scoreCp={0} />)
    expect(screen.getByText('+0.0')).toBeInTheDocument()
  })

  it('displays mate score for white', () => {
    render(<EvalBar scoreMate={3} />)
    expect(screen.getByText('M3')).toBeInTheDocument()
  })

  it('displays mate score for black', () => {
    render(<EvalBar scoreMate={-5} />)
    expect(screen.getByText('M-5')).toBeInTheDocument()
  })

  it('prioritizes mate score over centipawn score', () => {
    render(<EvalBar scoreCp={150} scoreMate={2} />)
    expect(screen.getByText('M2')).toBeInTheDocument()
    expect(screen.queryByText('+1.5')).not.toBeInTheDocument()
  })

  it('fills bar proportionally for positive eval', () => {
    render(<EvalBar scoreCp={200} />)
    const meter = screen.getByRole('meter')
    // White portion should be greater than 50%
    const whiteFill = meter.querySelector('[data-testid="white-fill"]')
    expect(whiteFill).toBeInTheDocument()
  })

  it('fills bar proportionally for negative eval', () => {
    render(<EvalBar scoreCp={-200} />)
    const meter = screen.getByRole('meter')
    const whiteFill = meter.querySelector('[data-testid="white-fill"]')
    expect(whiteFill).toBeInTheDocument()
  })

  it('pins bar to edge for mate in favor of white', () => {
    render(<EvalBar scoreMate={1} />)
    const whiteFill = screen.getByTestId('white-fill')
    expect(whiteFill.style.height).toBe('100%')
  })

  it('pins bar to edge for mate in favor of black', () => {
    render(<EvalBar scoreMate={-1} />)
    const whiteFill = screen.getByTestId('white-fill')
    expect(whiteFill.style.height).toBe('0%')
  })

  it('clamps white percentage between 10% and 90% for centipawn scores', () => {
    render(<EvalBar scoreCp={5000} />)
    const whiteFill = screen.getByTestId('white-fill')
    expect(whiteFill.style.height).toBe('90%')
  })

  it('clamps white percentage minimum at 10% for large negative scores', () => {
    render(<EvalBar scoreCp={-5000} />)
    const whiteFill = screen.getByTestId('white-fill')
    expect(whiteFill.style.height).toBe('10%')
  })

  it('respects black orientation by flipping the display', () => {
    const { container } = render(<EvalBar scoreCp={150} orientation="black" />)
    // When orientation is black, the bar should be rotated
    const bar = container.querySelector('[data-orientation="black"]')
    expect(bar).toBeInTheDocument()
  })

  it('defaults to white orientation', () => {
    const { container } = render(<EvalBar scoreCp={150} />)
    const bar = container.querySelector('[data-orientation="white"]')
    expect(bar).toBeInTheDocument()
  })
})
