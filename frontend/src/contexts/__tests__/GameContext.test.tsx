import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { renderHook, act } from '@testing-library/react'
import { GameProvider, useGameContext } from '../GameContext'

const STARTING_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'

// ---------------------------------------------------------------------------
// GameProvider renders children
// ---------------------------------------------------------------------------

describe('GameProvider', () => {
  it('renders children', () => {
    render(
      <GameProvider>
        <div data-testid="child">Hello</div>
      </GameProvider>,
    )

    expect(screen.getByTestId('child')).toHaveTextContent('Hello')
  })

  it('accepts a custom startFen option', () => {
    const customFen = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1'

    function FenDisplay(): React.JSX.Element {
      const { fen } = useGameContext()
      return <div data-testid="fen">{fen}</div>
    }

    render(
      <GameProvider startFen={customFen}>
        <FenDisplay />
      </GameProvider>,
    )

    expect(screen.getByTestId('fen')).toHaveTextContent(customFen)
  })
})

// ---------------------------------------------------------------------------
// useGameContext
// ---------------------------------------------------------------------------

describe('useGameContext', () => {
  it('throws when used outside GameProvider', () => {
    // Suppress console.error for expected error
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    expect(() => {
      renderHook(() => useGameContext())
    }).toThrow('useGameContext must be used within a GameProvider')

    consoleSpy.mockRestore()
  })

  it('provides initial game state', () => {
    const { result } = renderHook(() => useGameContext(), {
      wrapper: ({ children }: { children: React.ReactNode }) => (
        <GameProvider>{children}</GameProvider>
      ),
    })

    expect(result.current.fen).toBe(STARTING_FEN)
    expect(result.current.moves).toEqual([])
    expect(result.current.isGameOver).toBe(false)
    expect(result.current.result).toBeNull()
    expect(result.current.turn).toBe('w')
    expect(typeof result.current.makeMove).toBe('function')
    expect(typeof result.current.addEngineMove).toBe('function')
    expect(typeof result.current.reset).toBe('function')
  })

  it('allows making moves through context', () => {
    const { result } = renderHook(() => useGameContext(), {
      wrapper: ({ children }: { children: React.ReactNode }) => (
        <GameProvider>{children}</GameProvider>
      ),
    })

    act(() => {
      result.current.makeMove('e2e4')
    })

    expect(result.current.fen).toBe('rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1')
    expect(result.current.moves).toHaveLength(1)
    expect(result.current.turn).toBe('b')
  })

  it('allows adding engine moves with eval data through context', () => {
    const { result } = renderHook(() => useGameContext(), {
      wrapper: ({ children }: { children: React.ReactNode }) => (
        <GameProvider>{children}</GameProvider>
      ),
    })

    act(() => {
      result.current.addEngineMove('e2e4', { scoreCp: 30, depth: 20 })
    })

    expect(result.current.moves[0]).toEqual({
      san: 'e4',
      uci: 'e2e4',
      scoreCp: 30,
      depth: 20,
    })
  })

  it('shares state between sibling consumers', () => {
    function MoveButton(): React.JSX.Element {
      const { makeMove } = useGameContext()
      return <button onClick={() => makeMove('e2e4')}>Move</button>
    }

    function FenDisplay(): React.JSX.Element {
      const { fen } = useGameContext()
      return <div data-testid="fen">{fen}</div>
    }

    render(
      <GameProvider>
        <MoveButton />
        <FenDisplay />
      </GameProvider>,
    )

    expect(screen.getByTestId('fen')).toHaveTextContent(STARTING_FEN)

    act(() => {
      screen.getByRole('button', { name: 'Move' }).click()
    })

    expect(screen.getByTestId('fen')).toHaveTextContent(
      'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1',
    )
  })
})
