import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { SPRTDashboard } from './SPRTDashboard'
import type { SPRTTest } from '../../types/api'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeTest(overrides: Partial<SPRTTest> = {}): SPRTTest {
  return {
    id: 't1',
    engine_a: 'EngineA',
    engine_b: 'EngineB',
    time_control: {
      type: 'movetime',
      movetime_ms: 1000,
      wtime_ms: null,
      btime_ms: null,
      winc_ms: null,
      binc_ms: null,
      moves_to_go: null,
      depth: null,
      nodes: null,
    },
    elo0: 0,
    elo1: 5,
    alpha: 0.05,
    beta: 0.05,
    created_at: '2025-01-01T00:00:00Z',
    status: 'running',
    wins: 10,
    losses: 5,
    draws: 3,
    llr: 1.5,
    result: null,
    completed_at: null,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('SPRTDashboard', () => {
  it('shows empty message when no tests', () => {
    render(<SPRTDashboard tests={[]} onCancel={vi.fn()} onSelect={vi.fn()} />)
    expect(screen.getByText('No SPRT tests yet.')).toBeInTheDocument()
  })

  it('renders a table row per test with W/D/L, LLR, engine pair', () => {
    const tests = [
      makeTest({
        id: 't1',
        engine_a: 'Stockfish',
        engine_b: 'Leela',
        wins: 10,
        draws: 3,
        losses: 5,
        llr: 1.5,
      }),
      makeTest({
        id: 't2',
        engine_a: 'A',
        engine_b: 'B',
        status: 'completed',
        wins: 50,
        draws: 20,
        losses: 30,
        llr: 2.95,
        result: 'H1',
      }),
    ]
    render(<SPRTDashboard tests={tests} onCancel={vi.fn()} onSelect={vi.fn()} />)

    expect(screen.getByText('Stockfish vs Leela')).toBeInTheDocument()
    expect(screen.getByText('A vs B')).toBeInTheDocument()
    expect(screen.getByText('10 / 3 / 5')).toBeInTheDocument()
    expect(screen.getByText('50 / 20 / 30')).toBeInTheDocument()
    expect(screen.getByText('1.50')).toBeInTheDocument()
    expect(screen.getByText('2.95')).toBeInTheDocument()
    expect(screen.getByText('H1')).toBeInTheDocument()
  })

  it('displays status badges with correct text', () => {
    const tests = [
      makeTest({ id: 't1', status: 'running' }),
      makeTest({ id: 't2', status: 'completed', result: 'H0' }),
      makeTest({ id: 't3', status: 'cancelled' }),
    ]
    render(<SPRTDashboard tests={tests} onCancel={vi.fn()} onSelect={vi.fn()} />)

    expect(screen.getByText('running')).toBeInTheDocument()
    expect(screen.getByText('completed')).toBeInTheDocument()
    expect(screen.getByText('cancelled')).toBeInTheDocument()
  })

  it('shows Cancel button only for running tests', () => {
    const tests = [
      makeTest({ id: 't1', status: 'running' }),
      makeTest({ id: 't2', status: 'completed', result: 'H1' }),
    ]
    render(<SPRTDashboard tests={tests} onCancel={vi.fn()} onSelect={vi.fn()} />)

    const cancelButtons = screen.getAllByRole('button', { name: 'Cancel' })
    expect(cancelButtons).toHaveLength(1)
  })

  it('calls onCancel with test id when Cancel is clicked', async () => {
    const onCancel = vi.fn()
    const tests = [makeTest({ id: 't1', status: 'running' })]
    render(<SPRTDashboard tests={tests} onCancel={onCancel} onSelect={vi.fn()} />)

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalledWith('t1')
  })

  it('calls onSelect with test id when a row is clicked', async () => {
    const onSelect = vi.fn()
    const tests = [makeTest({ id: 't1' })]
    render(<SPRTDashboard tests={tests} onCancel={vi.fn()} onSelect={onSelect} />)

    await userEvent.click(screen.getByText('EngineA vs EngineB'))
    expect(onSelect).toHaveBeenCalledWith('t1')
  })

  it('Cancel click does not trigger row onSelect', async () => {
    const onSelect = vi.fn()
    const onCancel = vi.fn()
    const tests = [makeTest({ id: 't1', status: 'running' })]
    render(<SPRTDashboard tests={tests} onCancel={onCancel} onSelect={onSelect} />)

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalledWith('t1')
    expect(onSelect).not.toHaveBeenCalled()
  })

  it('shows em-dash for result when result is null', () => {
    const tests = [makeTest({ id: 't1', result: null })]
    render(<SPRTDashboard tests={tests} onCancel={vi.fn()} onSelect={vi.fn()} />)

    expect(screen.getByText('—')).toBeInTheDocument()
  })
})
