import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'

type Run = {
  schema_version: string
  run_id: string
  created_at: string
  status: string
  input?: { case_ref: string; language: 'fr' | 'en'; trigger: string }
  artifacts?: { report_markdown?: string; handout_markdown?: string }
  policy_violations?: unknown[]
}

type RunEvent = {
  type: string
  ts?: string
  step?: string
  message?: string
  [k: string]: unknown
}

function App() {
  const apiBase = useMemo(() => {
    return import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
  }, [])

  const [language, setLanguage] = useState<'fr' | 'en'>('fr')
  const [caseRef, setCaseRef] = useState('case_000042')
  const [run, setRun] = useState<Run | null>(null)
  const [events, setEvents] = useState<Array<{ id: number; data: RunEvent }>>([])
  const [error, setError] = useState<string | null>(null)
  const [isStarting, setIsStarting] = useState(false)
  const esRef = useRef<EventSource | null>(null)
  const seenIdsRef = useRef<Set<number>>(new Set())

  async function startRun() {
    setError(null)
    setRun(null)
    setEvents([])
    seenIdsRef.current = new Set()
    setIsStarting(true)

    try {
      const resp = await fetch(`${apiBase}/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ case_ref: caseRef, language, trigger: 'manual' }),
      })

      if (!resp.ok) {
        setError(`Failed to start run (${resp.status}).`)
        return
      }

      const r = (await resp.json()) as Run
      setRun(r)

      // Subscribe to SSE.
      esRef.current?.close()
      const es = new EventSource(`${apiBase}/runs/${r.run_id}/events`)
      esRef.current = es

      function pushEvent(evt: MessageEvent<string>) {
        try {
          const id = Number.parseInt(evt.lastEventId || '0', 10) || Date.now()
          if (seenIdsRef.current.has(id)) return
          seenIdsRef.current.add(id)

          const data = JSON.parse(evt.data) as RunEvent
          setEvents((prev) => prev.concat([{ id, data }]))
        } catch {
          // ignore bad event payloads
        }
      }

      es.addEventListener('step_started', (evt) => {
        const e = evt as MessageEvent<string>
        pushEvent(e)
      })
      es.addEventListener('step_completed', (evt) => {
        const e = evt as MessageEvent<string>
        pushEvent(e)
      })
      es.addEventListener('finalized', async (evt) => {
        const e = evt as MessageEvent<string>
        pushEvent(e)

        es.close()
        esRef.current = null

        // Refresh run details (artifacts).
        try {
          const runResp = await fetch(`${apiBase}/runs/${r.run_id}`)
          if (runResp.ok) setRun((await runResp.json()) as Run)
        } catch {
          // ignore refresh failure
        }
      })

      es.onerror = () => {
        // Avoid spamming errors; user can retry.
        setError(`SSE connection error (API: ${apiBase}).`)
      }
    } catch {
      setError(`Cannot reach API at ${apiBase}. Start it with: make api-dev`)
    } finally {
      setIsStarting(false)
    }
  }

  useEffect(() => {
    return () => {
      esRef.current?.close()
    }
  }, [])

  return (
    <>
      <div className="app">
        <header className="header">
          <div>
            <div className="title">PharmAssist AI — Kaggle Demo</div>
            <div className="subtitle">
              Day 3: Orchestrator + SSE progress (synthetic-only, no PHI)
            </div>
          </div>
          <div className="controls">
            <label>
              Lang
              <select value={language} onChange={(e) => setLanguage(e.target.value as 'fr' | 'en')}>
                <option value="fr">FR</option>
                <option value="en">EN</option>
              </select>
            </label>
            <label>
              Case
              <input value={caseRef} onChange={(e) => setCaseRef(e.target.value)} />
            </label>
            <button onClick={startRun} disabled={isStarting}>
              {isStarting ? 'Starting...' : 'Start run'}
            </button>
          </div>
        </header>

        {error ? <div className="error">{error}</div> : null}

        <main className="grid">
          <section className="panel">
            <div className="panelTitle">Run</div>
            {run ? (
              <div className="kv">
                <div>
                  <span className="k">run_id</span>
                  <span className="v mono">{run.run_id}</span>
                </div>
                <div>
                  <span className="k">status</span>
                  <span className="v">{run.status}</span>
                </div>
                <div>
                  <span className="k">created_at</span>
                  <span className="v mono">{run.created_at}</span>
                </div>
              </div>
            ) : (
              <div className="muted">No run yet.</div>
            )}
          </section>

          <section className="panel">
            <div className="panelTitle">Progress Events (SSE)</div>
            <div className="events">
              {events.length === 0 ? (
                <div className="muted">Start a run to see live events.</div>
              ) : (
                events.map((e) => (
                  <div key={e.id} className="event">
                    <div className="eventTop">
                      <span className="badge">{e.data.type}</span>
                      <span className="mono small">{e.data.ts ?? ''}</span>
                    </div>
                    <div className="eventMsg">
                      {e.data.step ? <span className="mono">{e.data.step}: </span> : null}
                      {e.data.message ?? ''}
                    </div>
                  </div>
                ))
              )}
            </div>
          </section>

          <section className="panel">
            <div className="panelTitle">Artifacts (placeholder)</div>
            <div className="artifact">
              <div className="artifactTitle">Report</div>
              <pre className="mono pre">{run?.artifacts?.report_markdown ?? '—'}</pre>
            </div>
            <div className="artifact">
              <div className="artifactTitle">Handout</div>
              <pre className="mono pre">{run?.artifacts?.handout_markdown ?? '—'}</pre>
            </div>
          </section>
        </main>
      </div>
    </>
  )
}

export default App
