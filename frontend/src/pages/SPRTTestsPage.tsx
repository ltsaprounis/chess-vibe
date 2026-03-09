import { useCallback, useEffect, useState } from 'react'
import {
  fetchEngines,
  fetchOpeningBooks,
  fetchSPRTTests,
  createSPRTTest,
  cancelSPRTTest,
} from '../services/api'
import type { Engine, OpeningBook, SPRTTest } from '../services/api'
import { useWebSocket } from '../hooks/useWebSocket'
import { SPRTDashboard } from '../components/SPRTDashboard/SPRTDashboard'

// ---------------------------------------------------------------------------
// WebSocket message types
// ---------------------------------------------------------------------------

interface WsProgressMessage {
  type: 'progress'
  wins: number
  losses: number
  draws: number
  llr: number
  lower_bound: number
  upper_bound: number
}

interface WsCompleteMessage {
  type: 'complete'
  result: string
  total_games: number
  llr: number
}

interface WsErrorMessage {
  type: 'error'
  message: string
}

type WsSprtMessage = WsProgressMessage | WsCompleteMessage | WsErrorMessage

// ---------------------------------------------------------------------------
// Live progress state
// ---------------------------------------------------------------------------

interface LiveProgress {
  wins: number
  losses: number
  draws: number
  llr: number
  lowerBound: number
  upperBound: number
  result: string | null
  totalGames: number | null
}

// ---------------------------------------------------------------------------
// Help text component
// ---------------------------------------------------------------------------

function HelpText({ children }: { children: React.ReactNode }): React.JSX.Element {
  return <p className="mt-1 text-xs text-gray-500">{children}</p>
}

// ---------------------------------------------------------------------------
// Form validation
// ---------------------------------------------------------------------------

interface FormErrors {
  engineA?: string
  engineB?: string
  timeControl?: string
  elo?: string
  alpha?: string
  beta?: string
}

function validateForm(
  engineA: string,
  engineB: string,
  timeControl: string,
  elo0: number,
  elo1: number,
  alpha: number,
  beta: number,
): FormErrors {
  const errors: FormErrors = {}
  if (!engineA) errors.engineA = 'Engine A is required'
  if (!engineB) errors.engineB = 'Engine B is required'
  if (!timeControl.trim()) errors.timeControl = 'Time control is required'
  if (elo1 <= elo0) errors.elo = 'Elo1 must be greater than Elo0'
  if (alpha <= 0 || alpha >= 1) errors.alpha = 'Alpha must be between 0 and 1'
  if (beta <= 0 || beta >= 1) errors.beta = 'Beta must be between 0 and 1'
  return errors
}

// ---------------------------------------------------------------------------
// LLR Bar component
// ---------------------------------------------------------------------------

interface LLRBarProps {
  llr: number
  lowerBound: number
  upperBound: number
}

function LLRBar({ llr, lowerBound, upperBound }: LLRBarProps): React.JSX.Element {
  const range = upperBound - lowerBound
  const pct = range > 0 ? Math.max(0, Math.min(100, ((llr - lowerBound) / range) * 100)) : 50
  const barColor =
    llr >= upperBound ? 'bg-green-500' : llr <= lowerBound ? 'bg-red-500' : 'bg-blue-500'

  return (
    <div className="mt-2">
      <div className="flex justify-between text-xs text-gray-400">
        <span>H0 ({lowerBound.toFixed(2)})</span>
        <span>LLR: {llr.toFixed(2)}</span>
        <span>H1 ({upperBound.toFixed(2)})</span>
      </div>
      <div
        className="mt-1 h-4 w-full rounded bg-gray-700"
        role="progressbar"
        aria-valuenow={llr}
        aria-valuemin={lowerBound}
        aria-valuemax={upperBound}
      >
        <div className={`h-full rounded ${barColor}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SPRTTestsPage
// ---------------------------------------------------------------------------

export function SPRTTestsPage(): React.JSX.Element {
  // Dashboard state
  const [tests, setTests] = useState<SPRTTest[]>([])
  const [engines, setEngines] = useState<Engine[]>([])
  const [books, setBooks] = useState<OpeningBook[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Form state
  const [showForm, setShowForm] = useState(false)
  const [engineA, setEngineA] = useState('')
  const [commitA, setCommitA] = useState('')
  const [engineB, setEngineB] = useState('')
  const [commitB, setCommitB] = useState('')
  const [timeControl, setTimeControl] = useState('movetime=1000')
  const [elo0, setElo0] = useState(0)
  const [elo1, setElo1] = useState(5)
  const [alpha, setAlpha] = useState(0.05)
  const [beta, setBeta] = useState(0.05)
  const [selectedBook, setSelectedBook] = useState('')
  const [concurrency, setConcurrency] = useState(1)
  const [formErrors, setFormErrors] = useState<FormErrors>({})
  const [submitting, setSubmitting] = useState(false)

  // Live progress state
  const [activeTestId, setActiveTestId] = useState<string | null>(null)
  const [liveProgress, setLiveProgress] = useState<LiveProgress | null>(null)
  const [wsUrl, setWsUrl] = useState<string | null>(null)

  // -----------------------------------------------------------------------
  // Fetch initial data
  // -----------------------------------------------------------------------

  useEffect(() => {
    let cancelled = false
    const load = async (): Promise<void> => {
      try {
        const [enginesData, booksData, testsData] = await Promise.all([
          fetchEngines(),
          fetchOpeningBooks(),
          fetchSPRTTests(),
        ])
        if (!cancelled) {
          setEngines(enginesData)
          setBooks(booksData)
          setTests(testsData)
          setLoading(false)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load data')
          setLoading(false)
        }
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  // -----------------------------------------------------------------------
  // WebSocket for live progress
  // -----------------------------------------------------------------------

  const handleWsMessage = useCallback(
    (msg: WsSprtMessage): void => {
      if (msg.type === 'progress') {
        setLiveProgress({
          wins: msg.wins,
          losses: msg.losses,
          draws: msg.draws,
          llr: msg.llr,
          lowerBound: msg.lower_bound,
          upperBound: msg.upper_bound,
          result: null,
          totalGames: null,
        })
      } else if (msg.type === 'complete') {
        setLiveProgress((prev) => ({
          wins: prev?.wins ?? 0,
          losses: prev?.losses ?? 0,
          draws: prev?.draws ?? 0,
          llr: msg.llr,
          lowerBound: prev?.lowerBound ?? 0,
          upperBound: prev?.upperBound ?? 0,
          result: msg.result,
          totalGames: msg.total_games,
        }))
        // Update test in list
        setTests((prev) =>
          prev.map((t) =>
            t.id === activeTestId
              ? { ...t, status: 'completed', result: msg.result, llr: msg.llr }
              : t,
          ),
        )
        setWsUrl(null)
      } else if (msg.type === 'error') {
        setError(msg.message)
        setWsUrl(null)
      }
    },
    [activeTestId],
  )

  useWebSocket<WsSprtMessage>(wsUrl, {
    onMessage: handleWsMessage,
    reconnect: false,
  })

  // -----------------------------------------------------------------------
  // Actions
  // -----------------------------------------------------------------------

  const handleSelectTest = useCallback(
    (id: string): void => {
      setActiveTestId(id)
      const test = tests.find((t) => t.id === id)
      if (test?.status === 'running') {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
        setWsUrl(`${protocol}//${window.location.host}/ws/sprt/${id}`)
        setLiveProgress(null)
      } else {
        setWsUrl(null)
        setLiveProgress(null)
      }
    },
    [tests],
  )

  const handleCancel = useCallback(
    async (id: string): Promise<void> => {
      try {
        await cancelSPRTTest(id)
        setTests((prev) => prev.map((t) => (t.id === id ? { ...t, status: 'cancelled' } : t)))
        if (activeTestId === id) {
          setWsUrl(null)
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to cancel test')
      }
    },
    [activeTestId],
  )

  const handleSubmit = useCallback(async (): Promise<void> => {
    const errors = validateForm(engineA, engineB, timeControl, elo0, elo1, alpha, beta)
    setFormErrors(errors)
    if (Object.keys(errors).length > 0) return

    const engineASpec = commitA.trim() ? `${engineA}:${commitA.trim()}` : engineA
    const engineBSpec = commitB.trim() ? `${engineB}:${commitB.trim()}` : engineB

    setSubmitting(true)
    try {
      const created = await createSPRTTest({
        engine_a: engineASpec,
        engine_b: engineBSpec,
        time_control: timeControl,
        elo0,
        elo1,
        alpha,
        beta,
        book_path: selectedBook || null,
        concurrency,
      })
      // Add new test to list and start watching
      const newTest: SPRTTest = {
        id: created.id,
        engine_a: engineA,
        engine_b: engineB,
        time_control: {
          type: 'movetime',
          movetime_ms: null,
          wtime_ms: null,
          btime_ms: null,
          winc_ms: null,
          binc_ms: null,
          moves_to_go: null,
          depth: null,
          nodes: null,
        },
        elo0,
        elo1,
        alpha,
        beta,
        created_at: new Date().toISOString(),
        status: 'running',
        wins: 0,
        losses: 0,
        draws: 0,
        llr: 0,
        result: null,
        completed_at: null,
      }
      setTests((prev) => [newTest, ...prev])
      setActiveTestId(created.id)
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      setWsUrl(`${protocol}//${window.location.host}/ws/sprt/${created.id}`)
      setLiveProgress(null)
      setShowForm(false)
      setSubmitting(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create test')
      setSubmitting(false)
    }
  }, [
    engineA,
    commitA,
    engineB,
    commitB,
    timeControl,
    elo0,
    elo1,
    alpha,
    beta,
    selectedBook,
    concurrency,
  ])

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------

  if (loading) {
    return (
      <main>
        <h1 className="text-3xl font-bold">SPRT Tests</h1>
        <p className="mt-4 text-gray-400">Loading…</p>
      </main>
    )
  }

  return (
    <main>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-3xl font-bold">SPRT Tests</h1>
        <button
          className="rounded bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-500"
          onClick={() => setShowForm(!showForm)}
        >
          {showForm ? 'Close Form' : 'New Test'}
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded bg-red-900 p-3 text-red-200" role="alert">
          {error}
        </div>
      )}

      {/* ------ Create form ------ */}
      {showForm && (
        <div className="mb-6 rounded border border-gray-700 bg-gray-800 p-4">
          <h2 className="mb-4 text-xl font-semibold">Create SPRT Test</h2>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div>
              <label htmlFor="engineA" className="mb-1 block text-sm text-gray-300">
                Engine A
              </label>
              <select
                id="engineA"
                className="w-full rounded bg-gray-700 px-3 py-2 text-white"
                value={engineA}
                onChange={(e) => setEngineA(e.target.value)}
              >
                <option value="">Select engine…</option>
                {engines.map((eng) => (
                  <option key={eng.id} value={eng.id}>
                    {eng.name}
                  </option>
                ))}
              </select>
              {formErrors.engineA && (
                <p className="mt-1 text-xs text-red-400">{formErrors.engineA}</p>
              )}
              <HelpText>Base engine to test against (the &quot;baseline&quot;).</HelpText>
            </div>

            <div>
              <label htmlFor="commitA" className="mb-1 block text-sm text-gray-300">
                Commit (Engine A)
              </label>
              <input
                id="commitA"
                type="text"
                placeholder="e.g. abc123 or HEAD~1"
                className="w-full rounded bg-gray-700 px-3 py-2 text-white placeholder-gray-500"
                value={commitA}
                onChange={(e) => setCommitA(e.target.value)}
              />
              <HelpText>
                Optional git commit SHA or ref. Leave blank to use the current working tree.
              </HelpText>
            </div>

            <div>
              <label htmlFor="engineB" className="mb-1 block text-sm text-gray-300">
                Engine B
              </label>
              <select
                id="engineB"
                className="w-full rounded bg-gray-700 px-3 py-2 text-white"
                value={engineB}
                onChange={(e) => setEngineB(e.target.value)}
              >
                <option value="">Select engine…</option>
                {engines.map((eng) => (
                  <option key={eng.id} value={eng.id}>
                    {eng.name}
                  </option>
                ))}
              </select>
              {formErrors.engineB && (
                <p className="mt-1 text-xs text-red-400">{formErrors.engineB}</p>
              )}
              <HelpText>Engine under test (the &quot;challenger&quot;).</HelpText>
            </div>

            <div>
              <label htmlFor="commitB" className="mb-1 block text-sm text-gray-300">
                Commit (Engine B)
              </label>
              <input
                id="commitB"
                type="text"
                placeholder="e.g. def456 or HEAD"
                className="w-full rounded bg-gray-700 px-3 py-2 text-white placeholder-gray-500"
                value={commitB}
                onChange={(e) => setCommitB(e.target.value)}
              />
              <HelpText>
                Optional git commit SHA or ref. Leave blank to use the current working tree.
              </HelpText>
            </div>

            <div>
              <label htmlFor="timeControl" className="mb-1 block text-sm text-gray-300">
                Time Control
              </label>
              <input
                id="timeControl"
                type="text"
                className="w-full rounded bg-gray-700 px-3 py-2 text-white"
                value={timeControl}
                onChange={(e) => setTimeControl(e.target.value)}
              />
              {formErrors.timeControl && (
                <p className="mt-1 text-xs text-red-400">{formErrors.timeControl}</p>
              )}
              <HelpText>
                Format: <code className="text-gray-400">movetime=MS</code>,{' '}
                <code className="text-gray-400">wtime=MS btime=MS winc=MS binc=MS</code>,{' '}
                <code className="text-gray-400">depth=N</code>, or{' '}
                <code className="text-gray-400">nodes=N</code>.
              </HelpText>
            </div>

            <div>
              <label htmlFor="book" className="mb-1 block text-sm text-gray-300">
                Opening Book
              </label>
              <select
                id="book"
                className="w-full rounded bg-gray-700 px-3 py-2 text-white"
                value={selectedBook}
                onChange={(e) => setSelectedBook(e.target.value)}
              >
                <option value="">None</option>
                {books.map((b) => (
                  <option key={b.id} value={b.path}>
                    {b.name}
                  </option>
                ))}
              </select>
              <HelpText>
                EPD or PGN opening book. Each opening is played twice with swapped colours.
              </HelpText>
            </div>

            <div>
              <label htmlFor="elo0" className="mb-1 block text-sm text-gray-300">
                Elo0
              </label>
              <input
                id="elo0"
                type="number"
                className="w-full rounded bg-gray-700 px-3 py-2 text-white"
                value={elo0}
                onChange={(e) => setElo0(Number(e.target.value))}
              />
              <HelpText>
                Null hypothesis Elo difference (H0). SPRT stops and accepts H0 if the true Elo gain
                is ≤ this value. Typically 0.
              </HelpText>
            </div>

            <div>
              <label htmlFor="elo1" className="mb-1 block text-sm text-gray-300">
                Elo1
              </label>
              <input
                id="elo1"
                type="number"
                className="w-full rounded bg-gray-700 px-3 py-2 text-white"
                value={elo1}
                onChange={(e) => setElo1(Number(e.target.value))}
              />
              {formErrors.elo && <p className="mt-1 text-xs text-red-400">{formErrors.elo}</p>}
              <HelpText>
                Alternative hypothesis Elo gain (H1). SPRT stops and accepts H1 if the true Elo gain
                is ≥ this value. Must be greater than Elo0.
              </HelpText>
            </div>

            <div>
              <label htmlFor="alpha" className="mb-1 block text-sm text-gray-300">
                Alpha
              </label>
              <input
                id="alpha"
                type="number"
                step="0.01"
                className="w-full rounded bg-gray-700 px-3 py-2 text-white"
                value={alpha}
                onChange={(e) => setAlpha(Number(e.target.value))}
              />
              {formErrors.alpha && <p className="mt-1 text-xs text-red-400">{formErrors.alpha}</p>}
              <HelpText>
                Type I error rate — probability of accepting H1 when H0 is true (false positive).
                Lower = more games. Typical: 0.05.
              </HelpText>
            </div>

            <div>
              <label htmlFor="beta" className="mb-1 block text-sm text-gray-300">
                Beta
              </label>
              <input
                id="beta"
                type="number"
                step="0.01"
                className="w-full rounded bg-gray-700 px-3 py-2 text-white"
                value={beta}
                onChange={(e) => setBeta(Number(e.target.value))}
              />
              {formErrors.beta && <p className="mt-1 text-xs text-red-400">{formErrors.beta}</p>}
              <HelpText>
                Type II error rate — probability of accepting H0 when H1 is true (false negative).
                Lower = more games. Typical: 0.05.
              </HelpText>
            </div>

            <div>
              <label htmlFor="concurrency" className="mb-1 block text-sm text-gray-300">
                Concurrency
              </label>
              <input
                id="concurrency"
                type="number"
                min="1"
                className="w-full rounded bg-gray-700 px-3 py-2 text-white"
                value={concurrency}
                onChange={(e) => setConcurrency(Number(e.target.value))}
              />
              <HelpText>
                Number of games to play in parallel. Each worker runs one game at a time.
              </HelpText>
            </div>
          </div>

          <button
            className="mt-4 rounded bg-green-700 px-4 py-2 text-sm font-semibold text-white hover:bg-green-600 disabled:opacity-50"
            onClick={() => void handleSubmit()}
            disabled={submitting}
          >
            {submitting ? 'Creating…' : 'Create Test'}
          </button>
        </div>
      )}

      {/* ------ Dashboard table ------ */}
      <SPRTDashboard
        tests={tests}
        onCancel={(id) => void handleCancel(id)}
        onSelect={handleSelectTest}
      />

      {/* ------ Live progress panel ------ */}
      {activeTestId && liveProgress && (
        <div className="mt-6 rounded border border-gray-700 bg-gray-800 p-4">
          <h2 className="mb-2 text-lg font-semibold">Live Progress — Test {activeTestId}</h2>
          <div className="grid grid-cols-3 gap-4 text-center">
            <div>
              <p className="text-2xl font-bold text-green-400">{liveProgress.wins}</p>
              <p className="text-xs text-gray-400">Wins</p>
            </div>
            <div>
              <p className="text-2xl font-bold text-gray-300">{liveProgress.draws}</p>
              <p className="text-xs text-gray-400">Draws</p>
            </div>
            <div>
              <p className="text-2xl font-bold text-red-400">{liveProgress.losses}</p>
              <p className="text-xs text-gray-400">Losses</p>
            </div>
          </div>
          <LLRBar
            llr={liveProgress.llr}
            lowerBound={liveProgress.lowerBound}
            upperBound={liveProgress.upperBound}
          />
          {liveProgress.result && (
            <p className="mt-3 text-center text-lg font-bold text-yellow-300">
              Result: {liveProgress.result}
              {liveProgress.totalGames !== null && ` (${liveProgress.totalGames} games)`}
            </p>
          )}
        </div>
      )}
    </main>
  )
}
