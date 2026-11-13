import { describe, expect, it } from 'vitest'
import { formatAnnotation, toWhitePerspective } from './whitePerspective'

const MINUS = '\u2212'

describe('toWhitePerspective', () => {
  it("negates a centipawn score from Black's side", () => {
    expect(toWhitePerspective({ score_cp: 110, score_mate: null }, true)).toEqual({ scoreCp: -110 })
  })

  it("negates a mate score from Black's side", () => {
    expect(toWhitePerspective({ score_cp: null, score_mate: 3 }, true)).toEqual({ scoreMate: -3 })
  })

  it("keeps a score from White's side as it is", () => {
    expect(toWhitePerspective({ score_cp: -50, score_mate: null }, false)).toEqual({ scoreCp: -50 })
    expect(toWhitePerspective({ score_cp: null, score_mate: -2 }, false)).toEqual({ scoreMate: -2 })
  })

  it('reads a null or missing field as no score of that kind', () => {
    expect(toWhitePerspective({ score_cp: null, score_mate: null }, false)).toEqual({})
    expect(toWhitePerspective({ score_cp: null, score_mate: null }, true)).toEqual({})
    expect(toWhitePerspective({}, true)).toEqual({})
  })

  it('keeps a score of 0, which is a score rather than a missing one', () => {
    expect(toWhitePerspective({ score_cp: 0, score_mate: null }, false)).toEqual({ scoreCp: 0 })
    expect(toWhitePerspective({ score_cp: null, score_mate: 0 }, false)).toEqual({ scoreMate: 0 })
    // Negated for Black, 0 becomes -0, which still reads as level.
    expect(formatAnnotation(toWhitePerspective({ score_cp: 0, score_mate: null }, true))).toBe(
      '(+0.0)',
    )
  })
})

describe('formatAnnotation', () => {
  it('shows a centipawn score in pawns, with a plus sign from zero up', () => {
    expect(formatAnnotation({ scoreCp: 110 })).toBe('(+1.1)')
    expect(formatAnnotation({ scoreCp: 0 })).toBe('(+0.0)')
  })

  it('writes a negative centipawn score with U+2212, as the eval bar does', () => {
    expect(formatAnnotation({ scoreCp: -110 })).toBe(`(${MINUS}1.1)`)
  })

  it('shows a mate score as M and the signed number of moves, as the eval bar does', () => {
    expect(formatAnnotation({ scoreMate: 3 })).toBe('(M3)')
    expect(formatAnnotation({ scoreMate: -3 })).toBe('(M-3)')
  })

  it('shows the mate score when there is a centipawn score too', () => {
    expect(formatAnnotation({ scoreCp: 500, scoreMate: 2 })).toBe('(M2)')
  })

  it('shows nothing when there is no score', () => {
    expect(formatAnnotation({})).toBeUndefined()
  })
})
