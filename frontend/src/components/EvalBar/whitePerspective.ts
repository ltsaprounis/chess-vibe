/**
 * Engine scores from White's side, for every page that shows them.
 *
 * Lives beside EvalBar because EvalBar and EvalGraph read every score as
 * White's advantage, the usual way to read an eval bar or graph. Converting
 * and formatting scores in one place is what keeps a score reading the same
 * on the Play page as on Game Replay.
 */

/**
 * A score as UCI reports it and `Move` stores it: from the side that made
 * the move. Either field is null or missing when the engine reported no
 * score of that kind, so a mate score comes with a null `score_cp`.
 */
export interface MoverRelativeScore {
  score_cp?: number | null
  score_mate?: number | null
}

/** A score from White's side, as EvalBar and EvalGraph read it. */
export interface WhitePerspectiveScore {
  scoreCp?: number
  scoreMate?: number
}

/**
 * Convert a score from the side that moved to White's side: a move played
 * by Black has its sign flipped. A null or missing field stays missing, so
 * a move without a score shows none rather than 0.
 */
export function toWhitePerspective(
  score: MoverRelativeScore,
  moverIsBlack: boolean,
): WhitePerspectiveScore {
  const { score_cp: cp, score_mate: mate } = score
  const scoreCp = cp == null ? undefined : moverIsBlack ? -cp : cp
  const scoreMate = mate == null ? undefined : moverIsBlack ? -mate : mate
  return { scoreCp, scoreMate }
}

/**
 * The move list's annotation for a score from White's side. Formats a score
 * as EvalBar's own formatScore does (mate before centipawns, U+2212 for a
 * negative centipawn score), in brackets, so the same score never reads
 * differently in the move list than on the eval bar.
 */
export function formatAnnotation(score: WhitePerspectiveScore): string | undefined {
  if (score.scoreMate !== undefined) return `(M${score.scoreMate})`
  if (score.scoreCp !== undefined) {
    const pawns = score.scoreCp / 100
    if (score.scoreCp >= 0) return `(+${pawns.toFixed(1)})`
    return `(\u2212${Math.abs(pawns).toFixed(1)})`
  }
  return undefined
}
