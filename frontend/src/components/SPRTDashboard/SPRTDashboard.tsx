import type { SPRTTest } from '../../types/api'

export interface SPRTDashboardProps {
  tests: SPRTTest[]
  onCancel: (id: string) => void
  onSelect: (id: string) => void
}

function statusBadge(status: string): React.JSX.Element {
  const colors: Record<string, string> = {
    running: 'bg-blue-600 text-blue-100',
    completed: 'bg-green-700 text-green-100',
    cancelled: 'bg-gray-600 text-gray-200',
  }
  const cls = colors[status] ?? 'bg-gray-600 text-gray-200'
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-semibold ${cls}`}>
      {status}
    </span>
  )
}

export function SPRTDashboard({
  tests,
  onCancel,
  onSelect,
}: SPRTDashboardProps): React.JSX.Element {
  if (tests.length === 0) {
    return <p className="text-gray-400">No SPRT tests yet.</p>
  }

  return (
    <table className="w-full text-left text-sm">
      <thead>
        <tr className="border-b border-gray-700 text-gray-400">
          <th className="px-3 py-2">Status</th>
          <th className="px-3 py-2">Engines</th>
          <th className="px-3 py-2">W / D / L</th>
          <th className="px-3 py-2">LLR</th>
          <th className="px-3 py-2">Result</th>
          <th className="px-3 py-2">Actions</th>
        </tr>
      </thead>
      <tbody>
        {tests.map((t) => (
          <tr
            key={t.id}
            className="cursor-pointer border-b border-gray-800 hover:bg-gray-800"
            onClick={() => onSelect(t.id)}
          >
            <td className="px-3 py-2">{statusBadge(t.status)}</td>
            <td className="px-3 py-2">
              {t.engine_a} vs {t.engine_b}
            </td>
            <td className="px-3 py-2">
              {t.wins} / {t.draws} / {t.losses}
            </td>
            <td className="px-3 py-2">{t.llr.toFixed(2)}</td>
            <td className="px-3 py-2">{t.result ?? '—'}</td>
            <td className="px-3 py-2">
              {t.status === 'running' && (
                <button
                  className="rounded bg-red-700 px-2 py-1 text-xs text-white hover:bg-red-600"
                  onClick={(e) => {
                    e.stopPropagation()
                    onCancel(t.id)
                  }}
                >
                  Cancel
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
