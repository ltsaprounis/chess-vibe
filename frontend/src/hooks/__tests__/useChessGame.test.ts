import { describe, it, expect } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useChessGame } from '../useChessGame'

const STARTING_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'

// ---------------------------------------------------------------------------
// Initial state
// ---------------------------------------------------------------------------

describe('initial state', () => {
  it('starts with the standard position FEN', () => {
    const { result } = renderHook(() => useChessGame())

    expect(result.current.fen).toBe(STARTING_FEN)
  })

  it('starts with an empty moves array', () => {
    const { result } = renderHook(() => useChessGame())

    expect(result.current.moves).toEqual([])
  })

  it('reports game is not over', () => {
    const { result } = renderHook(() => useChessGame())

    expect(result.current.isGameOver).toBe(false)
  })

  it('reports result as null', () => {
    const { result } = renderHook(() => useChessGame())

    expect(result.current.result).toBeNull()
  })

  it('reports white to move', () => {
    const { result } = renderHook(() => useChessGame())

    expect(result.current.turn).toBe('w')
  })

  it('returns PGN with headers but no move text initially', () => {
    const { result } = renderHook(() => useChessGame())

    expect(result.current.pgn).toContain('[Result "*"]')
    expect(result.current.pgn).not.toContain('1.')
  })
})

// ---------------------------------------------------------------------------
// Custom start FEN
// ---------------------------------------------------------------------------

describe('custom start FEN', () => {
  it('accepts a custom start FEN', () => {
    const customFen = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1'
    const { result } = renderHook(() => useChessGame({ startFen: customFen }))

    expect(result.current.fen).toBe(customFen)
    expect(result.current.turn).toBe('b')
  })
})

// ---------------------------------------------------------------------------
// Making legal moves
// ---------------------------------------------------------------------------

describe('makeMove', () => {
  it('returns true for a legal move', () => {
    const { result } = renderHook(() => useChessGame())

    let success = false
    act(() => {
      success = result.current.makeMove('e2e4')
    })

    expect(success).toBe(true)
  })

  it('updates FEN after a legal move', () => {
    const { result } = renderHook(() => useChessGame())

    act(() => {
      result.current.makeMove('e2e4')
    })

    expect(result.current.fen).toBe('rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1')
  })

  it('switches turn after a legal move', () => {
    const { result } = renderHook(() => useChessGame())

    act(() => {
      result.current.makeMove('e2e4')
    })

    expect(result.current.turn).toBe('b')
  })

  it('appends to the moves array with san and uci', () => {
    const { result } = renderHook(() => useChessGame())

    act(() => {
      result.current.makeMove('e2e4')
    })

    expect(result.current.moves).toHaveLength(1)
    expect(result.current.moves[0]).toEqual({
      san: 'e4',
      uci: 'e2e4',
    })
  })

  it('updates PGN after moves', () => {
    const { result } = renderHook(() => useChessGame())

    act(() => {
      result.current.makeMove('e2e4')
    })
    act(() => {
      result.current.makeMove('e7e5')
    })

    expect(result.current.pgn).toContain('e4')
    expect(result.current.pgn).toContain('e5')
  })

  it('handles pawn promotion', () => {
    // White pawn on e7, about to promote
    const promotionFen = '8/4P3/8/8/8/8/8/4K2k w - - 0 1'
    const { result } = renderHook(() => useChessGame({ startFen: promotionFen }))

    let success = false
    act(() => {
      success = result.current.makeMove('e7e8q')
    })

    expect(success).toBe(true)
    expect(result.current.moves[0].san).toContain('e8=Q')
  })
})

// ---------------------------------------------------------------------------
// Illegal moves
// ---------------------------------------------------------------------------

describe('illegal moves', () => {
  it('returns false for an illegal move', () => {
    const { result } = renderHook(() => useChessGame())

    let success = true
    act(() => {
      success = result.current.makeMove('e2e5')
    })

    expect(success).toBe(false)
  })

  it('does not update FEN for an illegal move', () => {
    const { result } = renderHook(() => useChessGame())

    act(() => {
      result.current.makeMove('e2e5')
    })

    expect(result.current.fen).toBe(STARTING_FEN)
  })

  it('does not add to moves array for an illegal move', () => {
    const { result } = renderHook(() => useChessGame())

    act(() => {
      result.current.makeMove('e2e5')
    })

    expect(result.current.moves).toHaveLength(0)
  })

  it('returns false for a completely invalid move string', () => {
    const { result } = renderHook(() => useChessGame())

    let success = true
    act(() => {
      success = result.current.makeMove('invalid')
    })

    expect(success).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// addEngineMove
// ---------------------------------------------------------------------------

describe('addEngineMove', () => {
  it('makes a move and records eval data', () => {
    const { result } = renderHook(() => useChessGame())

    act(() => {
      result.current.makeMove('e2e4')
    })

    let success = false
    act(() => {
      success = result.current.addEngineMove('e7e5', {
        scoreCp: -30,
        depth: 20,
        pv: ['e7e5', 'd2d4'],
      })
    })

    expect(success).toBe(true)
    expect(result.current.moves).toHaveLength(2)
    expect(result.current.moves[1]).toEqual({
      san: 'e5',
      uci: 'e7e5',
      scoreCp: -30,
      depth: 20,
      pv: ['e7e5', 'd2d4'],
    })
  })

  it('records scoreMate when provided', () => {
    const { result } = renderHook(() => useChessGame())

    act(() => {
      result.current.addEngineMove('e2e4', { scoreMate: 5 })
    })

    expect(result.current.moves[0]).toEqual({
      san: 'e4',
      uci: 'e2e4',
      scoreMate: 5,
    })
  })

  it('returns false for an illegal engine move', () => {
    const { result } = renderHook(() => useChessGame())

    let success = true
    act(() => {
      success = result.current.addEngineMove('e2e5')
    })

    expect(success).toBe(false)
  })

  it('works without eval data', () => {
    const { result } = renderHook(() => useChessGame())

    let success = false
    act(() => {
      success = result.current.addEngineMove('e2e4')
    })

    expect(success).toBe(true)
    expect(result.current.moves[0]).toEqual({
      san: 'e4',
      uci: 'e2e4',
    })
  })
})

// ---------------------------------------------------------------------------
// Game-over detection
// ---------------------------------------------------------------------------

describe('game-over detection', () => {
  it('detects checkmate (white wins)', () => {
    // Scholar's mate setup: just before Qxf7#
    const mateInOneFen = 'r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4'
    const { result } = renderHook(() => useChessGame({ startFen: mateInOneFen }))

    act(() => {
      result.current.makeMove('h5f7')
    })

    expect(result.current.isGameOver).toBe(true)
    expect(result.current.result).toBe('1-0')
  })

  it('detects checkmate (black wins)', () => {
    // Fool's mate setup: after 1. f3 e5 2. g4, black plays Qh4#
    const foolsMateFen = 'rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2'
    const { result } = renderHook(() => useChessGame({ startFen: foolsMateFen }))

    act(() => {
      result.current.makeMove('d8h4')
    })

    expect(result.current.isGameOver).toBe(true)
    expect(result.current.result).toBe('0-1')
  })

  it('detects stalemate', () => {
    // White Kc6, Qb5, Black Ka8. After Qb6, black is stalemated.
    const stalemateFen = 'k7/8/2K5/1Q6/8/8/8/8 w - - 0 1'
    const { result } = renderHook(() => useChessGame({ startFen: stalemateFen }))

    act(() => {
      result.current.makeMove('b5b6')
    })

    expect(result.current.isGameOver).toBe(true)
    expect(result.current.result).toBe('1/2-1/2')
  })

  it('detects draw by threefold repetition', () => {
    const { result } = renderHook(() => useChessGame())

    // Move knights back and forth to trigger repetition
    const moves = [
      'g1f3',
      'g8f6',
      'f3g1',
      'f6g8', // back to start (2nd time)
      'g1f3',
      'g8f6',
      'f3g1',
      'f6g8', // back to start (3rd time)
    ]

    for (const move of moves) {
      act(() => {
        result.current.makeMove(move)
      })
    }

    expect(result.current.isGameOver).toBe(true)
    expect(result.current.result).toBe('1/2-1/2')
  })

  it('detects draw by insufficient material', () => {
    // King vs King
    const kvkFen = '4k3/8/8/8/8/8/8/4K3 w - - 0 1'
    const { result } = renderHook(() => useChessGame({ startFen: kvkFen }))

    expect(result.current.isGameOver).toBe(true)
    expect(result.current.result).toBe('1/2-1/2')
  })
})

// ---------------------------------------------------------------------------
// Reset
// ---------------------------------------------------------------------------

describe('reset', () => {
  it('resets to starting position', () => {
    const { result } = renderHook(() => useChessGame())

    act(() => {
      result.current.makeMove('e2e4')
    })
    act(() => {
      result.current.makeMove('e7e5')
    })
    act(() => {
      result.current.reset()
    })

    expect(result.current.fen).toBe(STARTING_FEN)
    expect(result.current.moves).toEqual([])
    expect(result.current.isGameOver).toBe(false)
    expect(result.current.result).toBeNull()
    expect(result.current.turn).toBe('w')
    expect(result.current.pgn).not.toContain('1.')
  })

  it('resets to a custom FEN', () => {
    const customFen = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1'
    const { result } = renderHook(() => useChessGame())

    act(() => {
      result.current.makeMove('e2e4')
    })
    act(() => {
      result.current.reset(customFen)
    })

    expect(result.current.fen).toBe(customFen)
    expect(result.current.moves).toEqual([])
    expect(result.current.turn).toBe('b')
  })
})
