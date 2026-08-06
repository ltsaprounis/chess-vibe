/**
 * Move numbering for games that do not begin at the standard initial
 * position.
 *
 * Lives beside MoveList rather than inside it because more than the list
 * needs it: the replay page names a single move in its status line and picks
 * the eval sign per move from the same derivation. Keeping one source of
 * truth is what stops those displays disagreeing with each other.
 */

/**
 * The position a game's recorded moves begin from, in the two respects that
 * decide how those moves are numbered.
 *
 * Neither field can be assumed: SPRT games are seeded from opening books,
 * which supply arbitrary FENs — an odd-ply book line leaves *Black* to move,
 * and a line resumed mid-game starts at a full-move number above 1. Deriving
 * the numbering from move-index parity instead renders Black's move in
 * White's column and shifts every move number for the rest of the game.
 */
export interface StartPosition {
  /** True when Black is the side to move in the starting position. */
  blackToMove: boolean
  /** The starting position's FEN full-move counter (1 for a new game). */
  fullMoveNumber: number
}

/** The standard initial position: White to move, full move 1. */
export const STANDARD_START_POSITION: StartPosition = {
  blackToMove: false,
  fullMoveNumber: 1,
}

/**
 * Read the numbering-relevant fields out of a FEN — the active-colour field
 * and the full-move counter. A null, empty or malformed FEN falls back to
 * {@link STANDARD_START_POSITION}; `GameDetail.start_fen` is null for games
 * played from the standard position, so that fallback is the common path,
 * not just an error case.
 */
export function parseStartPosition(fen: string | null | undefined): StartPosition {
  const fields = fen?.trim().split(/\s+/) ?? []
  const fullMoveNumber = Number(fields[5])
  return {
    blackToMove: fields[1] === 'b',
    fullMoveNumber: Number.isInteger(fullMoveNumber) && fullMoveNumber >= 1 ? fullMoveNumber : 1,
  }
}

/**
 * Ply offset of `index` within its full move: 0 for a White move, 1 for a
 * Black one. Moves alternate colour strictly from the side to move in
 * `start`, so this is index parity shifted by who moves first.
 */
function plyOf(index: number, start: StartPosition): number {
  return index + (start.blackToMove ? 1 : 0)
}

/** True when the move at `index` was played by Black. */
export function isBlackMoveAt(index: number, start: StartPosition): boolean {
  return plyOf(index, start) % 2 === 1
}

/** The full-move number the move at `index` belongs to. */
export function fullMoveNumberAt(index: number, start: StartPosition): number {
  return start.fullMoveNumber + Math.floor(plyOf(index, start) / 2)
}

/**
 * Algebraic label for a single move — `12.` for White's, `12...` for
 * Black's.
 */
export function moveLabel(index: number, start: StartPosition): string {
  return `${fullMoveNumberAt(index, start)}${isBlackMoveAt(index, start) ? '...' : '.'}`
}
