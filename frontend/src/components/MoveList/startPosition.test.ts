import { describe, it, expect } from 'vitest'
import {
  STANDARD_START_POSITION,
  fullMoveNumberAt,
  isBlackMoveAt,
  moveLabel,
  parseStartPosition,
} from './startPosition'
import type { StartPosition } from './startPosition'

describe('parseStartPosition', () => {
  it('reads the standard initial position as White to move at full move 1', () => {
    expect(parseStartPosition('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1')).toEqual(
      STANDARD_START_POSITION,
    )
  })

  it('reads the active colour and full move from an opening-book FEN', () => {
    // Position after 1. e4 — Black to move, full move still 1 (the counter
    // increments only once Black has replied).
    expect(
      parseStartPosition('rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1'),
    ).toEqual({ blackToMove: true, fullMoveNumber: 1 })
  })

  it('reads a mid-game full-move number', () => {
    expect(parseStartPosition('8/8/8/8/8/8/8/K6k b - - 4 27')).toEqual({
      blackToMove: true,
      fullMoveNumber: 27,
    })
  })

  it('falls back to the standard start for a null or malformed FEN', () => {
    expect(parseStartPosition(null)).toEqual(STANDARD_START_POSITION)
    expect(parseStartPosition(undefined)).toEqual(STANDARD_START_POSITION)
    expect(parseStartPosition('')).toEqual(STANDARD_START_POSITION)
    expect(parseStartPosition('not a fen')).toEqual(STANDARD_START_POSITION)
  })

  it('falls back to full move 1 when the full-move field is not a positive integer', () => {
    expect(parseStartPosition('8/8/8/8/8/8/8/K6k w - - 0 0').fullMoveNumber).toBe(1)
    expect(parseStartPosition('8/8/8/8/8/8/8/K6k w - - 0 -3').fullMoveNumber).toBe(1)
    expect(parseStartPosition('8/8/8/8/8/8/8/K6k w - - 0 x').fullMoveNumber).toBe(1)
    expect(parseStartPosition('8/8/8/8/8/8/8/K6k w - - 0 2.5').fullMoveNumber).toBe(1)
  })

  it('tolerates irregular whitespace between FEN fields', () => {
    expect(parseStartPosition('  8/8/8/8/8/8/8/K6k   b  -  -  4  27  ')).toEqual({
      blackToMove: true,
      fullMoveNumber: 27,
    })
  })
})

describe('isBlackMoveAt', () => {
  it('alternates from White for a standard start', () => {
    expect(isBlackMoveAt(0, STANDARD_START_POSITION)).toBe(false)
    expect(isBlackMoveAt(1, STANDARD_START_POSITION)).toBe(true)
    expect(isBlackMoveAt(2, STANDARD_START_POSITION)).toBe(false)
  })

  it('alternates from Black for a Black-to-move start', () => {
    const start: StartPosition = { blackToMove: true, fullMoveNumber: 1 }
    expect(isBlackMoveAt(0, start)).toBe(true)
    expect(isBlackMoveAt(1, start)).toBe(false)
    expect(isBlackMoveAt(2, start)).toBe(true)
  })
})

describe('fullMoveNumberAt', () => {
  it('advances after every Black move from a standard start', () => {
    expect(fullMoveNumberAt(0, STANDARD_START_POSITION)).toBe(1)
    expect(fullMoveNumberAt(1, STANDARD_START_POSITION)).toBe(1)
    expect(fullMoveNumberAt(2, STANDARD_START_POSITION)).toBe(2)
  })

  it('advances immediately after the leading Black move of a Black-to-move start', () => {
    const start: StartPosition = { blackToMove: true, fullMoveNumber: 1 }
    expect(fullMoveNumberAt(0, start)).toBe(1)
    expect(fullMoveNumberAt(1, start)).toBe(2)
    expect(fullMoveNumberAt(2, start)).toBe(2)
  })

  it('counts up from the starting full move', () => {
    const start: StartPosition = { blackToMove: false, fullMoveNumber: 12 }
    expect(fullMoveNumberAt(0, start)).toBe(12)
    expect(fullMoveNumberAt(2, start)).toBe(13)
  })
})

describe('moveLabel', () => {
  it('labels White moves "N." and Black moves "N..." from a standard start', () => {
    expect(moveLabel(0, STANDARD_START_POSITION)).toBe('1.')
    expect(moveLabel(1, STANDARD_START_POSITION)).toBe('1...')
    expect(moveLabel(4, STANDARD_START_POSITION)).toBe('3.')
  })

  it('offsets by the starting side and full move', () => {
    const start: StartPosition = { blackToMove: true, fullMoveNumber: 12 }
    expect(moveLabel(0, start)).toBe('12...')
    expect(moveLabel(1, start)).toBe('13.')
    expect(moveLabel(2, start)).toBe('13...')
  })
})
