/**
 * Hand-rolled SVG line chart of centipawn evaluation over the course of a
 * game. No charting library is used — dependencies are exactly pinned by
 * policy and this component is simple enough not to warrant one.
 *
 * Contract with the caller (see GameReplayPage, which owns the mover→White
 * perspective conversion):
 * - Every point must already be in White's perspective — positive means
 *   White is better, negative means Black is better — matching the
 *   convention `EvalBar` uses. This component does no sign flipping.
 * - `scoreMate` takes priority over `scoreCp` on a point (mirrors EvalBar's
 *   own formatScore/computeWhitePercent logic): a forced mate clamps to
 *   +/-MATE_CLAMP_CP so it plots on the same fixed y-axis domain as
 *   centipawn scores rather than needing its own scale.
 * - A point with both fields null (no evaluation recorded for that move) is
 *   skipped: it contributes no vertex, so the line is drawn as a straight
 *   segment directly between its neighbours instead of dipping to zero.
 *   A set of points that are all null renders an empty-state message
 *   instead of an empty/broken chart.
 *
 * Y-axis domain is fixed at [-MATE_CLAMP_CP, MATE_CLAMP_CP] centipawns
 * (+/-10 pawns) rather than auto-scaled to the data, so the chart's shape is
 * comparable across games and an outlier score can't blow out the scale.
 * `x` is plotted on the raw half-move (ply) index into `points`, not a
 * full-move number — deriving full-move/side labels needs to know which
 * side moved first (see GameReplayPage's `toWhitePerspective`), which this
 * component intentionally has no knowledge of.
 *
 * A forced mate of exactly 0 (`scoreMate === 0`) is treated as bad for the
 * side that moved — i.e. `scoreMate > 0` clamps to the top (White better),
 * anything else (including `0` and `-0`) clamps to the bottom — matching
 * `EvalBar`'s own `computeWhitePercent` (`scoreMate > 0 ? 100 : 0`) so the
 * two components never disagree at that boundary.
 *
 * The current move is marked two ways so the indicator survives even when
 * that move has no recorded eval: an enlarged/coloured point when a value
 * exists, and a vertical rule (`data-testid="eval-graph-current-line"`) at
 * its x-position regardless.
 *
 * Test seam: the wrapper carries `data-testid="eval-graph"`; when data is
 * present the `<svg>` has `role="img"` with an accessible name, the
 * connecting line carries `data-testid="eval-graph-line"`, and each plotted
 * point carries `data-testid="eval-graph-point-{index}"` (index into
 * `points`) plus `data-current="true"/"false"`.
 */

export interface EvalGraphPoint {
  /** Centipawn score, White's perspective, or null if not recorded. */
  scoreCp: number | null
  /** Mate-in-N, White's perspective (positive = White mates), or null. */
  scoreMate: number | null
}

export interface EvalGraphProps {
  /** One point per half-move, in game order (index = move index). */
  points: EvalGraphPoint[]
  /** Index into `points` to highlight, or -1 for "no move played yet". */
  currentMoveIndex: number
  /** SVG viewBox height in logical (unitless) units; width is fixed. */
  height?: number
}

const VIEW_WIDTH = 600
const DEFAULT_HEIGHT = 150
const PADDING = 10
/** Centipawn magnitude a forced mate (and any evaluation outlier) clamps to. */
const MATE_CLAMP_CP = 1000

interface ResolvedEntry {
  index: number
  value: number
}

function resolveValue(point: EvalGraphPoint): number | null {
  if (point.scoreMate !== null) {
    // `> 0`, not `>= 0`: mirrors EvalBar's computeWhitePercent so a mate of
    // exactly 0 (and the `-0` a sign-flip of it can produce) plots at the
    // same edge the eval bar colours as Black-favoured, not White-favoured.
    return point.scoreMate > 0 ? MATE_CLAMP_CP : -MATE_CLAMP_CP
  }
  if (point.scoreCp !== null) {
    return Math.max(-MATE_CLAMP_CP, Math.min(MATE_CLAMP_CP, point.scoreCp))
  }
  return null
}

export function EvalGraph({
  points,
  currentMoveIndex,
  height = DEFAULT_HEIGHT,
}: EvalGraphProps): React.JSX.Element {
  const innerWidth = VIEW_WIDTH - PADDING * 2
  const innerHeight = height - PADDING * 2
  // Guard against divide-by-zero when there's only a single move to plot.
  const lastIndex = Math.max(points.length - 1, 1)

  const xAt = (index: number): number => PADDING + (index / lastIndex) * innerWidth
  const yAt = (value: number): number =>
    PADDING + ((MATE_CLAMP_CP - value) / (2 * MATE_CLAMP_CP)) * innerHeight

  const validEntries: ResolvedEntry[] = points.reduce<ResolvedEntry[]>((acc, point, index) => {
    const value = resolveValue(point)
    if (value !== null) acc.push({ index, value })
    return acc
  }, [])

  const linePath = validEntries
    .map((entry, i) => `${i === 0 ? 'M' : 'L'} ${xAt(entry.index)} ${yAt(entry.value)}`)
    .join(' ')

  // On a long game, per-move dots at a fixed radius overlap into a solid
  // band and drown out the current-move marker. Shrink both radii once
  // points are packed tighter than they are wide.
  const isDense = validEntries.length > 80
  const pointRadius = isDense ? 1.5 : 2
  const currentPointRadius = isDense ? 3 : 4

  const graphLabel =
    currentMoveIndex >= 0
      ? `Evaluation graph over ${points.length} moves, currently showing move ${currentMoveIndex + 1}`
      : `Evaluation graph over ${points.length} moves`

  return (
    <div data-testid="eval-graph" className="w-full">
      {validEntries.length === 0 ? (
        <p className="p-2 text-sm text-gray-400" data-testid="eval-graph-empty">
          No evaluation data
        </p>
      ) : (
        <svg
          role="img"
          aria-label={graphLabel}
          viewBox={`0 0 ${VIEW_WIDTH} ${height}`}
          className="h-auto w-full"
          preserveAspectRatio="none"
        >
          {/* Zero-eval reference line */}
          <line
            x1={PADDING}
            y1={yAt(0)}
            x2={VIEW_WIDTH - PADDING}
            y2={yAt(0)}
            className="stroke-gray-600"
            strokeDasharray="4 4"
            strokeWidth={1}
          />
          {/* Y-axis extremes, so the fixed +/-10 pawn domain is legible. */}
          <text x={PADDING + 2} y={PADDING + 8} className="fill-gray-500" style={{ fontSize: 8 }}>
            +10
          </text>
          <text x={PADDING + 2} y={yAt(0) - 2} className="fill-gray-500" style={{ fontSize: 8 }}>
            0
          </text>
          <text
            x={PADDING + 2}
            y={height - PADDING - 2}
            className="fill-gray-500"
            style={{ fontSize: 8 }}
          >
            -10
          </text>
          {validEntries.length > 1 && (
            <path
              d={linePath}
              fill="none"
              className="stroke-blue-400"
              strokeWidth={2}
              data-testid="eval-graph-line"
            />
          )}
          {/*
           * Current-move indicator, drawn independently of whether that
           * move has a plotted point below — a vertical rule always marks
           * the position, even for a null-eval current move that has no
           * circle of its own.
           */}
          {currentMoveIndex >= 0 && currentMoveIndex < points.length && (
            <line
              data-testid="eval-graph-current-line"
              x1={xAt(currentMoveIndex)}
              x2={xAt(currentMoveIndex)}
              y1={PADDING}
              y2={height - PADDING}
              className="stroke-yellow-400"
              strokeWidth={1}
              strokeDasharray="2 2"
            />
          )}
          {validEntries.map((entry) => {
            const isCurrent = entry.index === currentMoveIndex
            return (
              <circle
                key={entry.index}
                data-testid={`eval-graph-point-${entry.index}`}
                data-current={isCurrent}
                cx={xAt(entry.index)}
                cy={yAt(entry.value)}
                r={isCurrent ? currentPointRadius : pointRadius}
                className={isCurrent ? 'fill-yellow-400' : 'fill-blue-400'}
              />
            )
          })}
        </svg>
      )}
    </div>
  )
}
