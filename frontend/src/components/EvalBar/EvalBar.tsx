export interface EvalBarProps {
  scoreCp?: number
  scoreMate?: number | null
  orientation?: 'white' | 'black'
}

function formatScore(scoreCp?: number, scoreMate?: number | null): string {
  if (scoreMate != null) {
    return `M${scoreMate}`
  }
  const cp = scoreCp ?? 0
  const pawns = cp / 100
  if (cp >= 0) {
    return `+${pawns.toFixed(1)}`
  }
  return `\u2212${Math.abs(pawns).toFixed(1)}`
}

function computeWhitePercent(scoreCp?: number, scoreMate?: number | null): number {
  if (scoreMate != null) {
    return scoreMate > 0 ? 100 : 0
  }
  const cp = scoreCp ?? 0
  // Sigmoid-like mapping: 50 + cp/10, clamped to [10, 90]
  const raw = 50 + cp / 10
  return Math.min(90, Math.max(10, raw))
}

export function EvalBar({
  scoreCp,
  scoreMate,
  orientation = 'white',
}: EvalBarProps): React.JSX.Element {
  const whitePercent = computeWhitePercent(scoreCp, scoreMate)
  const displayText = formatScore(scoreCp, scoreMate)

  return (
    <div
      role="meter"
      aria-valuenow={whitePercent}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label="Engine evaluation"
      data-orientation={orientation}
      className="relative flex h-full w-6 flex-col overflow-hidden rounded border border-gray-600"
      style={orientation === 'black' ? { transform: 'rotate(180deg)' } : undefined}
    >
      {/* Black portion (top) */}
      <div
        className="bg-gray-800 transition-all duration-300"
        style={{ height: `${100 - whitePercent}%` }}
      />
      {/* White portion (bottom) */}
      <div
        data-testid="white-fill"
        className="bg-white transition-all duration-300"
        style={{ height: `${whitePercent}%` }}
      />
      {/* Score label */}
      <div
        className="absolute inset-0 flex items-center justify-center"
        style={orientation === 'black' ? { transform: 'rotate(180deg)' } : undefined}
      >
        <span className="text-[10px] font-bold text-gray-500 mix-blend-difference">
          {displayText}
        </span>
      </div>
    </div>
  )
}
