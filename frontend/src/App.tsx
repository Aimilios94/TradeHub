import { useEffect, useState } from 'react'
import { getJson, type Deps, type Source } from './api'

type Loaded<T> = { data?: T; error?: string }

function useApi<T>(path: string): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({})
  useEffect(() => {
    getJson<T>(path)
      .then((data) => setState({ data }))
      .catch((error: Error) => setState({ error: error.message }))
  }, [path])
  return state
}

function ErrorNote({ what, error }: { what: string; error: string }) {
  return (
    <p className="error" role="alert">
      {what} could not be loaded: {error}
    </p>
  )
}

export default function App() {
  const deps = useApi<Deps>('/health/deps')
  const sources = useApi<Source[]>('/api/sources')

  return (
    <main>
      <header>
        <h1>TradeHub</h1>
        <p className="muted">System status and source health. The Dashboard arrives in Phase 2.</p>
      </header>

      <section>
        <h2>Services</h2>
        {deps.error && <ErrorNote what="Service status" error={deps.error} />}
        {deps.data && (
          <ul className="services">
            {Object.entries(deps.data).map(([name, check]) => (
              <li key={name}>
                <span className={`badge ${check.ok ? 'working' : 'down'}`}>
                  {check.ok ? 'up' : 'down'}
                </span>
                <strong>{name}</strong>
                {check.error && <span className="muted"> {check.error}</span>}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2>Data sources</h2>
        {sources.error && <ErrorNote what="Sources" error={sources.error} />}
        {sources.data && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Status</th>
                  <th>Role</th>
                  <th>Last verified</th>
                  <th>Last success</th>
                  <th>Last error</th>
                </tr>
              </thead>
              <tbody>
                {sources.data.map((source) => (
                  <tr key={source.id} title={source.notes ?? undefined}>
                    <td>
                      <strong>{source.name}</strong>
                      <div className="muted">{source.kinds.join(', ')}</div>
                    </td>
                    <td>
                      <span className={`badge ${source.status}`}>{source.status}</span>
                    </td>
                    <td>{source.role}</td>
                    <td>{source.last_verified_on ?? 'never'}</td>
                    <td>{source.last_success_at ?? 'no run yet'}</td>
                    <td>{source.last_error ?? 'none'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  )
}
