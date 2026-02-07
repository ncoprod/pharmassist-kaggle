import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'

type FollowUpAnswer = { question_id: string; answer: string }

type FollowUpQuestion = {
  question_id: string
  question: string
  answer_type?: 'yes_no' | 'free_text' | 'number' | 'choice'
  choices?: string[]
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
    patient_ref?: string
    visit_ref?: string
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

type PatientSearchItem = {
  patient_ref: string
  demographics?: { age_years?: number; sex?: string }
}

type PatientDetail = {
  patient_ref: string
  llm_context: {
    demographics?: { age_years?: number; sex?: string }
    [k: string]: unknown
  }
}

type VisitSummary = {
  visit_ref: string
  occurred_at: string
  primary_domain?: string | null
  intents?: string[]
  presenting_problem?: string
}

type DocumentUploadReceipt = {
  schema_version: string
  status: 'ingested'
  doc_ref: string
  visit_ref: string
  patient_ref: string
  event_ref: string
  trigger: 'ocr_upload'
  language: 'fr' | 'en'
  sha256_12: string
  page_count: number
  text_length: number
  redaction_replacements: number
}

type DbCell = string | number | boolean | null | string[]
type DbRow = Record<string, DbCell>

type DbPreviewPayload = {
  schema_version: string
  table: string
  query: string
  limit: number
  count: number
  redacted: boolean
  columns: string[]
  rows: DbRow[]
}

function formatDbCell(value: DbCell): string {
  if (Array.isArray(value)) return value.join(', ')
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (value == null) return '—'
  return String(value)
}

function App() {
  const apiBase = useMemo(() => {
    return import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
  }, [])
  const apiKey = useMemo(() => {
    return import.meta.env.VITE_API_KEY ?? ''
  }, [])

  const [language, setLanguage] = useState<'fr' | 'en'>('fr')
  const [activeTab, setActiveTab] = useState<'run' | 'patients' | 'db'>('run')
  const [caseRef, setCaseRef] = useState('case_000042')
  const [run, setRun] = useState<Run | null>(null)
  const [events, setEvents] = useState<Array<{ id: number; data: RunEvent }>>([])
  const [followUpAnswers, setFollowUpAnswers] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [isStarting, setIsStarting] = useState(false)

  const [patientQuery, setPatientQuery] = useState('')
  const [patientResults, setPatientResults] = useState<PatientSearchItem[]>([])
  const [selectedPatient, setSelectedPatient] = useState<PatientDetail | null>(null)
  const [patientVisits, setPatientVisits] = useState<VisitSummary[]>([])
  const [isPatientsLoading, setIsPatientsLoading] = useState(false)
  const [prescriptionFile, setPrescriptionFile] = useState<File | null>(null)
  const [isUploadingPrescription, setIsUploadingPrescription] = useState(false)
  const [uploadReceipt, setUploadReceipt] = useState<DocumentUploadReceipt | null>(null)

  const [dbTables, setDbTables] = useState<string[]>([])
  const [dbTable, setDbTable] = useState('patients')
  const [dbQuery, setDbQuery] = useState('')
  const [dbAdminKey, setDbAdminKey] = useState(() => import.meta.env.VITE_ADMIN_DB_PREVIEW_KEY ?? '')
  const [dbPreview, setDbPreview] = useState<DbPreviewPayload | null>(null)
  const [isDbLoading, setIsDbLoading] = useState(false)

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

  function buildApiHeaders(opts?: { admin?: boolean; json?: boolean }): HeadersInit {
    const headers: HeadersInit = {}
    if (apiKey.trim()) headers['X-Api-Key'] = apiKey.trim()
    if (opts?.admin && dbAdminKey.trim()) headers['X-Admin-Key'] = dbAdminKey.trim()
    if (opts?.json) headers['Content-Type'] = 'application/json'
    return headers
  }

  const missingFollowUpCount = useMemo(() => {
    return followUpQuestions.filter((q) => {
      const v = followUpAnswers[q.question_id]
      return !v || v.trim() === ''
    }).length
  }, [followUpAnswers, followUpQuestions])

  useEffect(() => {
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

    function labelForChoice(choice: string): string {
      if (runLanguage === 'fr') {
        if (choice === 'mild') return 'Leger'
        if (choice === 'moderate') return 'Modere'
        if (choice === 'severe') return 'Severe'
      }

      if (q.question_id === 'q_primary_domain') {
        if (runLanguage === 'fr') {
          return choice === 'allergy_ent'
            ? 'Allergie / ORL'
            : choice === 'digestive'
              ? 'Digestif'
              : choice === 'skin'
                ? 'Peau'
                : choice === 'pain'
                  ? 'Douleur'
                  : choice === 'eye'
                    ? 'Oeil'
                    : choice === 'urology'
                      ? 'Urinaire'
                      : choice === 'respiratory'
                        ? 'Respiratoire'
                        : choice === 'other'
                          ? 'Autre'
                          : choice
        }

        return choice === 'allergy_ent'
          ? 'Allergy / ENT'
          : choice === 'digestive'
            ? 'Digestive'
            : choice === 'skin'
              ? 'Skin'
              : choice === 'pain'
                ? 'Pain'
                : choice === 'eye'
                  ? 'Eye'
                  : choice === 'urology'
                    ? 'Urinary'
                    : choice === 'respiratory'
                      ? 'Respiratory'
                      : choice === 'other'
                        ? 'Other'
                        : choice
      }

      return choice
    }

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

    if (q.answer_type === 'choice' && Array.isArray(q.choices) && q.choices.length > 0) {
      return (
        <select
          value={value}
          onChange={(e) => setFollowUpAnswer(q.question_id, e.target.value)}
          data-testid={testId}
        >
          <option value="">—</option>
          {q.choices.map((c) => (
            <option key={c} value={c}>
              {labelForChoice(c)}
            </option>
          ))}
        </select>
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

  async function startRun(opts?: {
    follow_up_answers?: FollowUpAnswer[]
    case_ref?: string
    patient_ref?: string
    visit_ref?: string
  }) {
    setError(null)
    setRun(null)
    setEvents([])
    seenIdsRef.current = new Set()
    setIsStarting(true)

    try {
      const body: Record<string, unknown> = {
        language,
        trigger: 'manual',
        ...(opts?.follow_up_answers ? { follow_up_answers: opts.follow_up_answers } : {}),
      }

      if (opts?.visit_ref) {
        body.visit_ref = opts.visit_ref
        if (opts.patient_ref) body.patient_ref = opts.patient_ref
      } else {
        body.case_ref = opts?.case_ref ?? caseRef
      }

      const resp = await fetch(`${apiBase}/runs`, {
        method: 'POST',
        headers: buildApiHeaders({ json: true }),
        body: JSON.stringify(body),
      })

      if (!resp.ok) {
        setError(`Failed to start run (${resp.status}).`)
        return
      }

      const r = (await resp.json()) as Run
      setRun(r)

      let streamToken = ''
      if (apiKey.trim()) {
        const tokenResp = await fetch(`${apiBase}/runs/${r.run_id}/events-token`, {
          method: 'POST',
          headers: buildApiHeaders(),
        })
        if (!tokenResp.ok) {
          setError(`Failed to open event stream token (${tokenResp.status}).`)
          return
        }
        const tokenPayload = (await tokenResp.json()) as { stream_token?: string }
        streamToken = (tokenPayload.stream_token ?? '').trim()
        if (!streamToken) {
          setError('Failed to open event stream token (missing token).')
          return
        }
      }

      esRef.current?.close()
      const sseParams = new URLSearchParams()
      if (streamToken) sseParams.set('stream_token', streamToken)
      const esUrl = `${apiBase}/runs/${r.run_id}/events${sseParams.toString() ? `?${sseParams.toString()}` : ''}`
      const es = new EventSource(esUrl)
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
        pushEvent(evt as MessageEvent<string>)
      })
      es.addEventListener('step_completed', (evt) => {
        pushEvent(evt as MessageEvent<string>)
      })
      es.addEventListener('finalized', async (evt) => {
        pushEvent(evt as MessageEvent<string>)

        es.close()
        esRef.current = null

        try {
          const runResp = await fetch(`${apiBase}/runs/${r.run_id}`, {
            headers: buildApiHeaders(),
          })
          if (runResp.ok) setRun((await runResp.json()) as Run)
        } catch {
          // ignore refresh failure
        }
      })

      es.onerror = () => {
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
    await startRun({
      follow_up_answers: payload,
      case_ref: run?.input?.case_ref,
      patient_ref: run?.input?.patient_ref,
      visit_ref: run?.input?.visit_ref,
    })
  }

  async function searchPatients() {
    const q = patientQuery.trim()
    if (!q) {
      setPatientResults([])
      return
    }
    setError(null)
    setIsPatientsLoading(true)
    try {
      const resp = await fetch(`${apiBase}/patients?query=${encodeURIComponent(q)}`, {
        headers: buildApiHeaders(),
      })
      if (!resp.ok) {
        setError(`Failed to search patients (${resp.status}).`)
        return
      }
      const data = (await resp.json()) as { patients?: PatientSearchItem[] }
      setPatientResults(Array.isArray(data.patients) ? data.patients : [])
    } catch {
      setError(`Cannot reach API at ${apiBase}.`)
    } finally {
      setIsPatientsLoading(false)
    }
  }

  async function openPatient(patientRef: string, opts?: { preserveUpload?: boolean }) {
    setError(null)
    setIsPatientsLoading(true)
    try {
      const [pResp, vResp] = await Promise.all([
        fetch(`${apiBase}/patients/${encodeURIComponent(patientRef)}`, {
          headers: buildApiHeaders(),
        }),
        fetch(`${apiBase}/patients/${encodeURIComponent(patientRef)}/visits`, {
          headers: buildApiHeaders(),
        }),
      ])
      if (!pResp.ok) {
        setError(`Failed to load patient (${pResp.status}).`)
        return
      }
      if (!vResp.ok) {
        setError(`Failed to load visits (${vResp.status}).`)
        return
      }
      const p = (await pResp.json()) as PatientDetail
      const v = (await vResp.json()) as { visits?: VisitSummary[] }
      setSelectedPatient(p)
      setPatientVisits(Array.isArray(v.visits) ? v.visits : [])
      if (!opts?.preserveUpload) {
        setUploadReceipt(null)
        setPrescriptionFile(null)
      }
    } catch {
      setError(`Cannot reach API at ${apiBase}.`)
    } finally {
      setIsPatientsLoading(false)
    }
  }

  async function startRunFromVisit(patientRef: string, visitRef: string) {
    setActiveTab('run')
    await startRun({ patient_ref: patientRef, visit_ref: visitRef })
  }

  async function uploadPrescriptionForSelectedPatient() {
    if (!selectedPatient) return
    if (!prescriptionFile) {
      setError('Select a PDF file first.')
      return
    }
    setError(null)
    setIsUploadingPrescription(true)
    try {
      const form = new FormData()
      form.append('patient_ref', selectedPatient.patient_ref)
      form.append('language', language)
      form.append('file', prescriptionFile)

      const resp = await fetch(`${apiBase}/documents/prescription`, {
        method: 'POST',
        headers: buildApiHeaders(),
        body: form,
      })
      if (!resp.ok) {
        let msg = `Failed to upload prescription (${resp.status}).`
        try {
          const payload = (await resp.json()) as { detail?: unknown }
          if (typeof payload?.detail === 'string') {
            msg = payload.detail
          } else if (payload?.detail && typeof payload.detail === 'object') {
            msg = JSON.stringify(payload.detail)
          }
        } catch {
          // keep default message
        }
        setError(msg)
        return
      }
      const receipt = (await resp.json()) as DocumentUploadReceipt
      setUploadReceipt(receipt)
      await openPatient(selectedPatient.patient_ref, { preserveUpload: true })
    } catch {
      setError(`Cannot reach API at ${apiBase}.`)
    } finally {
      setIsUploadingPrescription(false)
    }
  }

  async function loadDbTables() {
    setError(null)
    try {
      const resp = await fetch(`${apiBase}/admin/db-preview/tables`, {
        headers: buildApiHeaders({ admin: true }),
      })
      if (!resp.ok) {
        setError(`Failed to list DB tables (${resp.status}).`)
        return
      }
      const data = (await resp.json()) as { tables?: string[] }
      const tables = Array.isArray(data.tables) ? data.tables : []
      setDbTables(tables)
      if (tables.length > 0 && !tables.includes(dbTable)) setDbTable(tables[0])
    } catch {
      setError(`Cannot reach API at ${apiBase}.`)
    }
  }

  async function loadDbPreview() {
    setError(null)
    setIsDbLoading(true)
    try {
      const params = new URLSearchParams({ table: dbTable, limit: '50' })
      if (dbQuery.trim()) params.set('query', dbQuery.trim())
      const resp = await fetch(`${apiBase}/admin/db-preview?${params.toString()}`, {
        headers: buildApiHeaders({ admin: true }),
      })
      if (!resp.ok) {
        setError(`Failed to load DB preview (${resp.status}).`)
        return
      }
      const data = (await resp.json()) as DbPreviewPayload
      setDbPreview(data)
    } catch {
      setError(`Cannot reach API at ${apiBase}.`)
    } finally {
      setIsDbLoading(false)
    }
  }

  useEffect(() => {
    if (activeTab !== 'db') return
    if (dbTables.length === 0) {
      void loadDbTables().then(() => {
        void loadDbPreview()
      })
      return
    }
    void loadDbPreview()
    // intentionally reload preview when tab becomes active
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab])

  return (
    <>
      <div className="app">
        <header className="header">
          <div>
            <div className="title">PharmAssist AI — Kaggle Demo</div>
            <div className="subtitle">
              Feb 8: Patients + DB viewer + ordonnance scan (synthetic-only, no PHI)
            </div>
          </div>
          <div className="controls">
            <div className="tabs">
              <button
                className={`tab ${activeTab === 'run' ? 'tabActive' : ''}`}
                onClick={() => setActiveTab('run')}
                data-testid="tab-run"
              >
                Run
              </button>
              <button
                className={`tab ${activeTab === 'patients' ? 'tabActive' : ''}`}
                onClick={() => setActiveTab('patients')}
                data-testid="tab-patients"
              >
                Patients
              </button>
              <button
                className={`tab ${activeTab === 'db' ? 'tabActive' : ''}`}
                onClick={() => setActiveTab('db')}
                data-testid="tab-db"
              >
                DB
              </button>
            </div>
            <label>
              Lang
              <select value={language} onChange={(e) => setLanguage(e.target.value as 'fr' | 'en')}>
                <option value="fr">FR</option>
                <option value="en">EN</option>
              </select>
            </label>
            {activeTab === 'run' ? (
              <>
                <label>
                  Case
                  <input value={caseRef} onChange={(e) => setCaseRef(e.target.value)} />
                </label>
                <button onClick={() => void startRun()} disabled={isStarting} data-testid="start-run">
                  {isStarting ? 'Starting...' : 'Start run'}
                </button>
              </>
            ) : null}
          </div>
        </header>

        {error ? (
          <div className="error" data-testid="error-banner">
            {error}
          </div>
        ) : null}

        {activeTab === 'patients' ? (
          <main className="grid">
            <section className="panel">
              <div className="panelTitleRow">
                <div className="panelTitle">Patients</div>
              </div>
              <div className="row">
                <input
                  value={patientQuery}
                  onChange={(e) => setPatientQuery(e.target.value)}
                  placeholder="pt_0000…"
                  data-testid="patient-search"
                />
                <button
                  className="printBtn"
                  onClick={() => void searchPatients()}
                  disabled={isPatientsLoading}
                  data-testid="patient-search-btn"
                >
                  {isPatientsLoading ? 'Searching…' : 'Search'}
                </button>
              </div>
              <div className="muted">Search by `patient_ref` prefix (synthetic-only).</div>

              <div className="patientsList">
                {patientResults.length === 0 ? (
                  <div className="muted">No results.</div>
                ) : (
                  patientResults.map((p) => (
                    <div key={p.patient_ref} className="qCard">
                      <div className="qHeader">
                        <div className="qText mono">{p.patient_ref}</div>
                        <button
                          className="printBtn"
                          onClick={() => void openPatient(p.patient_ref)}
                          data-testid={`patient-result-${p.patient_ref}`}
                        >
                          Open
                        </button>
                      </div>
                      <div className="muted">
                        age={p.demographics?.age_years ?? '—'} sex={p.demographics?.sex ?? '—'}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </section>

            <section className="panel">
              <div className="panelTitle">Patient</div>
              {selectedPatient ? (
                <>
                  <div className="kv">
                    <div>
                      <span className="k">patient_ref</span>
                      <span className="v mono" data-testid="patient-detail-ref">
                        {selectedPatient.patient_ref}
                      </span>
                    </div>
                    <div>
                      <span className="k">age</span>
                      <span className="v">{selectedPatient.llm_context?.demographics?.age_years ?? '—'}</span>
                    </div>
                    <div>
                      <span className="k">sex</span>
                      <span className="v">{selectedPatient.llm_context?.demographics?.sex ?? '—'}</span>
                    </div>
                  </div>

                  <div className="subTitle">Scan ordonnance (PDF text-layer)</div>
                  <div className="row">
                    <input
                      type="file"
                      accept="application/pdf,.pdf"
                      onChange={(e) => {
                        setPrescriptionFile(e.target.files?.[0] ?? null)
                      }}
                      data-testid="patient-prescription-file"
                    />
                    <button
                      className="printBtn"
                      onClick={() => void uploadPrescriptionForSelectedPatient()}
                      disabled={isUploadingPrescription}
                      data-testid="patient-prescription-upload-btn"
                    >
                      {isUploadingPrescription ? 'Uploading…' : 'Upload PDF'}
                    </button>
                    {uploadReceipt ? (
                      <button
                        className="printBtn"
                        onClick={() =>
                          void startRunFromVisit(selectedPatient.patient_ref, uploadReceipt.visit_ref)
                        }
                        data-testid="patient-prescription-start-run"
                      >
                        Start run (uploaded)
                      </button>
                    ) : null}
                  </div>
                  {uploadReceipt ? (
                    <div className="muted" data-testid="patient-prescription-receipt">
                      doc_ref={uploadReceipt.doc_ref} visit_ref={uploadReceipt.visit_ref} pages=
                      {uploadReceipt.page_count} redactions={uploadReceipt.redaction_replacements}
                    </div>
                  ) : null}

                  <div className="subTitle">Visits</div>
                  <div className="patientsList">
                    {patientVisits.length === 0 ? (
                      <div className="muted">No visits.</div>
                    ) : (
                      patientVisits.map((v) => (
                        <div key={v.visit_ref} className="qCard">
                          <div className="qHeader">
                            <div className="qText">
                              {v.occurred_at} — {v.presenting_problem || v.primary_domain || '—'}
                            </div>
                            <button
                              className="printBtn"
                              onClick={() => void startRunFromVisit(selectedPatient.patient_ref, v.visit_ref)}
                              data-testid={`start-run-visit-${v.visit_ref}`}
                            >
                              Start run
                            </button>
                          </div>
                          <div className="muted">
                            domain={v.primary_domain ?? '—'} intents={(v.intents ?? []).join(', ') || '—'}
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </>
              ) : (
                <div className="muted">Select a patient from the search results.</div>
              )}
            </section>
          </main>
        ) : activeTab === 'db' ? (
          <main className="grid">
            <section className="panel">
              <div className="panelTitle">DB Viewer (Read-only, redacted)</div>
              <div className="row">
                <label>
                  Table
                  <select
                    value={dbTable}
                    onChange={(e) => setDbTable(e.target.value)}
                    data-testid="db-table-select"
                  >
                    {dbTables.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Query
                  <input
                    value={dbQuery}
                    onChange={(e) => setDbQuery(e.target.value)}
                    placeholder="prefix..."
                    data-testid="db-query-input"
                  />
                </label>
                <label>
                  Admin key
                  <input
                    value={dbAdminKey}
                    onChange={(e) => setDbAdminKey(e.target.value)}
                    placeholder="optional"
                    data-testid="db-admin-key-input"
                  />
                </label>
                <button
                  className="printBtn"
                  onClick={() => void loadDbPreview()}
                  disabled={isDbLoading}
                  data-testid="db-load-btn"
                >
                  {isDbLoading ? 'Loading…' : 'Load'}
                </button>
              </div>
              <div className="muted">
                Redacted preview only. No raw OCR/PDF text and no PHI payloads are exposed.
              </div>
            </section>

            <section className="panel">
              <div className="panelTitleRow">
                <div className="panelTitle">Rows</div>
                <div className="muted small" data-testid="db-preview-count">
                  {dbPreview ? `${dbPreview.table}: ${dbPreview.rows.length}/${dbPreview.count}` : '—'}
                </div>
              </div>
              {!dbPreview || dbPreview.rows.length === 0 ? (
                <div className="muted">No rows.</div>
              ) : (
                <div className="dbTableWrap">
                  <table className="dbTable" data-testid="db-table-grid">
                    <thead>
                      <tr>
                        {dbPreview.columns.map((c) => (
                          <th key={c}>{c}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {dbPreview.rows.map((r, idx) => (
                        <tr key={`${dbPreview.table}-${idx}`} data-testid="db-row">
                          {dbPreview.columns.map((c) => (
                            <td key={`${idx}-${c}`} className="mono small">
                              {formatDbCell((r[c] ?? null) as DbCell)}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </main>
        ) : (
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
                          {typeof q.priority === 'number' ? <span className="qPrio">P{q.priority}</span> : null}
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

                  <div className="subTitle">{runLanguage === 'fr' ? 'Alertes de securite' : 'Safety warnings'}</div>
                  {safetyWarnings.length === 0 ? (
                    <div className="muted small">—</div>
                  ) : (
                    <div className="warningList">
                      {safetyWarnings.map((w, idx) => (
                        <div key={`${w.code}-${w.related_product_sku ?? ''}-${idx}`} className="warning">
                          <span className={w.severity === 'BLOCKER' ? 'sev sevBlocker' : 'sev sevWarn'}>
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

                  <div className="subTitle">{runLanguage === 'fr' ? 'Top produits' : 'Top products'}</div>
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

                  <div className="subTitle">{runLanguage === 'fr' ? 'Sources (offline corpus)' : 'Sources (offline corpus)'}</div>
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
                        {runLanguage === 'fr' ? 'Audit (trace redactee)' : 'Audit (redacted trace)'}
                        <span className="muted small mono"> {trace.events.length} events</span>
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
                            {e.rule_id ? <div className="muted small mono">rule: {e.rule_id}</div> : null}
                            {e.tool_name ? <div className="muted small mono">tool: {e.tool_name}</div> : null}
                            {e.result_summary ? <div className="muted small">{e.result_summary}</div> : null}
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
        )}
      </div>
    </>
  )
}

export default App
