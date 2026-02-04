import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'

type FollowUpAnswer = { question_id: string; answer: string }

type FollowUpQuestion = {
  question_id: string
  question: string
  answer_type?: 'yes_no' | 'free_text' | 'number'
  reason?: string
  priority?: number
}

type RankedProduct = {
  product_sku: string
  score_0_100: number
  why: string
  evidence_refs?: string[]
}

type SafetyWarning = {
  code: string
  message: string
  severity: 'BLOCKER' | 'WARN'
  related_product_sku?: string
}

type Escalation = {
  recommended: boolean
  reason: string
  suggested_service: string
}

type EvidenceItem = {
  evidence_id: string
  title: string
  publisher: string
  url: string
  summary: string
}

type TraceViolation = {
  code: string
  severity: 'BLOCKER' | 'WARN'
  json_path: string
  message: string
}

type TraceEvent = {
  type: string
  ts?: string
  step?: string
  message?: string
  tool_name?: string
  result_summary?: string
  rule_id?: string
  severity?: string
  violation?: TraceViolation
}

type Trace = {
  trace_id: string
  run_id: string
  events: TraceEvent[]
}

type Recommendation = {
  follow_up_questions: FollowUpQuestion[]
  ranked_products?: RankedProduct[]
  safety_warnings?: SafetyWarning[]
  escalation?: Escalation
  confidence?: number
}

const EMPTY_FOLLOW_UP_ANSWERS: FollowUpAnswer[] = []
const EMPTY_FOLLOW_UP_QUESTIONS: FollowUpQuestion[] = []

type Run = {
  schema_version: string
  run_id: string
  created_at: string
  status: string
  input?: {
    case_ref: string
    language: 'fr' | 'en'
    trigger: string
    follow_up_answers?: FollowUpAnswer[]
  }
  artifacts?: {
    report_markdown?: string
    handout_markdown?: string
    recommendation?: Recommendation
    evidence_items?: EvidenceItem[]
    trace?: Trace
  }
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
  const [followUpAnswers, setFollowUpAnswers] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [isStarting, setIsStarting] = useState(false)
  const esRef = useRef<EventSource | null>(null)
  const seenIdsRef = useRef<Set<number>>(new Set())

  const followUpQuestions = useMemo(() => {
    return run?.artifacts?.recommendation?.follow_up_questions ?? EMPTY_FOLLOW_UP_QUESTIONS
  }, [run?.artifacts?.recommendation?.follow_up_questions])
  const needsMoreInfo = run?.status === 'needs_more_info' && followUpQuestions.length > 0
  const runLanguage = run?.input?.language ?? language
  const rankedProducts = run?.artifacts?.recommendation?.ranked_products ?? []
  const safetyWarnings = run?.artifacts?.recommendation?.safety_warnings ?? []
  const escalation = run?.artifacts?.recommendation?.escalation
  const evidenceItems = run?.artifacts?.evidence_items ?? []
  const trace = run?.artifacts?.trace

  const missingFollowUpCount = useMemo(() => {
    return followUpQuestions.filter((q) => {
      const v = followUpAnswers[q.question_id]
      return !v || v.trim() === ''
    }).length
  }, [followUpAnswers, followUpQuestions])

  useEffect(() => {
    // Pre-fill on reruns; also clears stale answers when the run_id changes.
    const next: Record<string, string> = {}
    for (const a of run?.input?.follow_up_answers ?? EMPTY_FOLLOW_UP_ANSWERS) {
      if (a?.question_id) next[a.question_id] = a.answer ?? ''
    }
    setFollowUpAnswers(next)
  }, [run?.input?.follow_up_answers, run?.run_id])

  function setFollowUpAnswer(questionId: string, value: string) {
    setFollowUpAnswers((prev) => ({ ...prev, [questionId]: value }))
  }

  function renderFollowUpInput(q: FollowUpQuestion) {
    const value = followUpAnswers[q.question_id] ?? ''
    const testId = `follow-up-answer-${q.question_id}`

    if (q.answer_type === 'yes_no') {
      return (
        <select
          value={value}
          onChange={(e) => setFollowUpAnswer(q.question_id, e.target.value)}
          data-testid={testId}
        >
          <option value="">—</option>
          <option value="yes">{runLanguage === 'fr' ? 'Oui' : 'Yes'}</option>
          <option value="no">{runLanguage === 'fr' ? 'Non' : 'No'}</option>
        </select>
      )
    }

    if (q.answer_type === 'number') {
      return (
        <input
          type="number"
          inputMode="numeric"
          value={value}
          onChange={(e) => setFollowUpAnswer(q.question_id, e.target.value)}
          data-testid={testId}
        />
      )
    }

    return (
      <textarea
        value={value}
        onChange={(e) => setFollowUpAnswer(q.question_id, e.target.value)}
        data-testid={testId}
        rows={3}
      />
    )
  }

  async function startRun(opts?: { follow_up_answers?: FollowUpAnswer[] }) {
    setError(null)
    setRun(null)
    setEvents([])
    seenIdsRef.current = new Set()
    setIsStarting(true)

    try {
      const resp = await fetch(`${apiBase}/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          case_ref: caseRef,
          language,
          trigger: 'manual',
          ...(opts?.follow_up_answers ? { follow_up_answers: opts.follow_up_answers } : {}),
        }),
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
      const origin = window.location.origin
      setError(
        `Cannot reach API at ${apiBase} (network/CORS). UI origin: ${origin}. If the API is running, check CORS allowlist.`,
      )
    } finally {
      setIsStarting(false)
    }
  }

  useEffect(() => {
    return () => {
      esRef.current?.close()
    }
  }, [])

  async function rerunWithFollowUpAnswers() {
    if (!needsMoreInfo) return
    const payload: FollowUpAnswer[] = followUpQuestions.map((q) => ({
      question_id: q.question_id,
      answer: (followUpAnswers[q.question_id] ?? '').trim(),
    }))
    await startRun({ follow_up_answers: payload })
  }

  return (
    <>
      <div className="app">
        <header className="header">
          <div>
            <div className="title">PharmAssist AI — Kaggle Demo</div>
            <div className="subtitle">
              Day 5: Follow-up questions + rerun (synthetic-only, no PHI)
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
            <button onClick={() => void startRun()} disabled={isStarting} data-testid="start-run">
              {isStarting ? 'Starting...' : 'Start run'}
            </button>
          </div>
        </header>

        {error ? (
          <div className="error" data-testid="error-banner">
            {error}
          </div>
        ) : null}

        <main className="grid">
          <section className="panel">
            <div className="panelTitle">Run</div>
            {run ? (
              <div className="kv">
                <div>
                  <span className="k">run_id</span>
                  <span className="v mono" data-testid="run-id">
                    {run.run_id}
                  </span>
                </div>
                <div>
                  <span className="k">status</span>
                  <span className="v" data-testid="run-status">
                    {run.status}
                  </span>
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

          <section className="panel" data-testid="follow-up-panel">
            <div className="panelTitle">Follow-up</div>
            {!run ? (
              <div className="muted">No run yet.</div>
            ) : followUpQuestions.length === 0 ? (
              <div className="muted">No follow-up questions.</div>
            ) : (
              <>
                {needsMoreInfo ? (
                  <div className="callout" data-testid="needs-more-info">
                    {runLanguage === 'fr'
                      ? 'Informations manquantes : repondez aux questions pour continuer.'
                      : 'This run needs more information to proceed.'}
                  </div>
                ) : null}

                <div className="followupList">
                  {followUpQuestions.map((q) => (
                    <div
                      key={q.question_id}
                      className="qCard"
                      data-testid={`follow-up-q-${q.question_id}`}
                    >
                      <div className="qHeader">
                        <div className="qText">{q.question}</div>
                        {typeof q.priority === 'number' ? (
                          <span className="qPrio">P{q.priority}</span>
                        ) : null}
                      </div>
                      {q.reason ? <div className="qReason">{q.reason}</div> : null}
                      <div className="qInput">{renderFollowUpInput(q)}</div>
                    </div>
                  ))}
                </div>

                {needsMoreInfo ? (
                  <div className="followupActions">
                    <button
                      onClick={rerunWithFollowUpAnswers}
                      disabled={isStarting || missingFollowUpCount > 0}
                      data-testid="follow-up-rerun"
                    >
                      {isStarting
                        ? runLanguage === 'fr'
                          ? 'Demarrage...'
                          : 'Starting...'
                        : runLanguage === 'fr'
                          ? 'Relancer avec reponses'
                          : 'Re-run with answers'}
                    </button>
                    {missingFollowUpCount > 0 ? (
                      <div className="muted small">
                        {runLanguage === 'fr'
                          ? `Reponse(s) manquante(s) : ${missingFollowUpCount}.`
                          : `Missing ${missingFollowUpCount} answer(s).`}
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </>
            )}
          </section>

          <section className="panel" data-testid="recommendation-panel">
            <div className="panelTitle">Recommendation</div>
            {!run ? (
              <div className="muted">No run yet.</div>
            ) : needsMoreInfo ? (
              <div className="muted">
                {runLanguage === 'fr'
                  ? 'Completez les questions de suivi pour afficher des recommandations.'
                  : 'Complete follow-up questions to show recommendations.'}
              </div>
            ) : (
              <>
                {escalation?.recommended ? (
                  <div className="callout">
                    <div className="qText">
                      {runLanguage === 'fr' ? 'Escalade recommandee' : 'Escalation recommended'}
                    </div>
                    <div className="qReason">{escalation.reason}</div>
                    <div className="muted small">{escalation.suggested_service}</div>
                  </div>
                ) : null}

                <div className="subTitle">
                  {runLanguage === 'fr' ? 'Alertes de securite' : 'Safety warnings'}
                </div>
                {safetyWarnings.length === 0 ? (
                  <div className="muted small">—</div>
                ) : (
                  <div className="warningList">
                    {safetyWarnings.map((w, idx) => (
                      <div key={`${w.code}-${w.related_product_sku ?? ''}-${idx}`} className="warning">
                        <span
                          className={
                            w.severity === 'BLOCKER' ? 'sev sevBlocker' : 'sev sevWarn'
                          }
                        >
                          {w.severity}
                        </span>
                        <span className="warningMsg">
                          {w.message}
                          {w.related_product_sku ? ` (${w.related_product_sku})` : ''}
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                <div className="subTitle">
                  {runLanguage === 'fr' ? 'Top produits' : 'Top products'}
                </div>
                {rankedProducts.length === 0 ? (
                  <div className="muted small">—</div>
                ) : (
                  <div className="productList">
                    {rankedProducts.map((p) => (
                      <div key={p.product_sku} className="productCard">
                        <div className="productTop">
                          <span className="mono">{p.product_sku}</span>
                          <span className="score">{p.score_0_100}</span>
                        </div>
                        <div className="productWhy">{p.why}</div>
                      </div>
                    ))}
                  </div>
                )}

                <div className="subTitle">
                  {runLanguage === 'fr' ? 'Sources (offline corpus)' : 'Sources (offline corpus)'}
                </div>
                {evidenceItems.length === 0 ? (
                  <div className="muted small">—</div>
                ) : (
                  <div className="evidenceList">
                    {evidenceItems.map((ev) => (
                      <a
                        key={ev.evidence_id}
                        className="evidenceItem"
                        href={ev.url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        <div className="evidenceTop">
                          <span className="evidenceTitle">{ev.title}</span>
                          <span className="mono small">{ev.evidence_id}</span>
                        </div>
                        <div className="muted small">{ev.publisher}</div>
                      </a>
                    ))}
                  </div>
                )}

                {trace?.events?.length ? (
                  <details className="auditDetails">
                    <summary className="auditSummary">
                      {runLanguage === 'fr'
                        ? 'Audit (trace redactee)'
                        : 'Audit (redacted trace)'}
                      <span className="muted small mono">
                        {' '}
                        {trace.events.length} events
                      </span>
                    </summary>
                    <div className="auditList">
                      {trace.events.map((e, idx) => (
                        <div key={`${e.type}-${idx}`} className="auditEvent">
                          <div className="auditTop">
                            <span className="badge">{e.type}</span>
                            <span className="muted small mono">
                              {e.step ? `${e.step} · ` : ''}
                              {e.ts ?? ''}
                            </span>
                          </div>
                          {e.rule_id ? (
                            <div className="muted small mono">rule: {e.rule_id}</div>
                          ) : null}
                          {e.tool_name ? (
                            <div className="muted small mono">tool: {e.tool_name}</div>
                          ) : null}
                          {e.result_summary ? (
                            <div className="muted small">{e.result_summary}</div>
                          ) : null}
                          {e.violation ? (
                            <div className="muted small">
                              {e.violation.severity}: {e.violation.code} @ {e.violation.json_path}
                            </div>
                          ) : null}
                          {e.message ? <div className="auditMsg">{e.message}</div> : null}
                        </div>
                      ))}
                    </div>
                  </details>
                ) : null}
              </>
            )}
          </section>

          <section className="panel printPanel">
            <div className="panelTitleRow">
              <div className="panelTitle">Artifacts</div>
              <button
                className="printBtn"
                onClick={() => window.print()}
                disabled={!run || run.status !== 'completed'}
                data-testid="print"
              >
                {runLanguage === 'fr' ? 'Imprimer' : 'Print'}
              </button>
            </div>
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
