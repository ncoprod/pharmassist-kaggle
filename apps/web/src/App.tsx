import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import { MarkdownArticle } from './components/MarkdownArticle'

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
  product_name?: string
  score_0_100: number
  why: string
  evidence_refs?: string[]
}

type SafetyWarning = {
  code: string
  message: string
  severity: 'BLOCKER' | 'WARN'
  related_product_sku?: string
  related_product_name?: string
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

type Prebrief = {
  top_actions?: string[]
  top_risks?: string[]
  top_questions?: string[]
  what_changed?: string[]
  new_rx_delta?: string[]
}

type PlannerStep = {
  step_id: string
  kind: 'counseling_question' | 'safety_check' | 'otc_suggestion' | 'escalation' | 'evidence_review'
  title: string
  detail: string
  evidence_refs: string[]
}

type PlannerPlan = {
  planner_version: string
  generated_at: string
  mode: 'agentic' | 'fallback_deterministic'
  fallback_used: boolean
  safety_checks: string[]
  steps: PlannerStep[]
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
    prebrief?: Prebrief
    plan?: PlannerPlan
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

type PatientAnalysisStatus = {
  schema_version: string
  patient_ref: string
  status: 'up_to_date' | 'refresh_pending' | 'running' | 'failed'
  changed_since_last_analysis: boolean
  latest_visit_ref?: string | null
  latest_visit_at?: string | null
  latest_run_id?: string | null
  latest_run_status?: string | null
  latest_run_at?: string | null
  last_error?: string | null
  message: string
  updated_at: string
}

type PatientInboxPayload = {
  schema_version: string
  generated_at: string
  count: number
  patients: PatientAnalysisStatus[]
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

type AtAGlance = {
  actions: string[]
  risks: string[]
  questions: string[]
  delta: string[]
  rxDelta: string[]
}

function topNUnique(items: string[], n: number): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const item of items) {
    const normalized = item.trim()
    if (!normalized) continue
    if (seen.has(normalized)) continue
    seen.add(normalized)
    out.push(normalized)
    if (out.length >= n) break
  }
  return out
}

function formatProductLabel(productSku?: string, productName?: string): string {
  const sku = (productSku ?? '').trim()
  const name = (productName ?? '').trim()
  if (name && sku) return `${name} (${sku})`
  return name || sku || '—'
}

function buildAtAGlance(run: Run | null, language: 'fr' | 'en'): AtAGlance {
  const prebrief = run?.artifacts?.prebrief
  if (prebrief) {
    return {
      actions: topNUnique(prebrief.top_actions ?? [], 3),
      risks: topNUnique(prebrief.top_risks ?? [], 3),
      questions: topNUnique(prebrief.top_questions ?? [], 3),
      delta: topNUnique(prebrief.what_changed ?? [], 3),
      rxDelta: topNUnique(prebrief.new_rx_delta ?? [], 3),
    }
  }

  const recommendation = run?.artifacts?.recommendation
  const actionsRaw: string[] = []
  const risksRaw: string[] = []
  const questionsRaw: string[] = []
  const deltaRaw: string[] = []
  const rxDeltaRaw: string[] = []

  if (recommendation?.escalation?.recommended) {
    actionsRaw.push(
      language === 'fr'
        ? `Escalade recommandee: ${recommendation.escalation.suggested_service}`
        : `Escalation recommended: ${recommendation.escalation.suggested_service}`,
    )
  }

  for (const p of recommendation?.ranked_products ?? []) {
    const label = formatProductLabel(p.product_sku, p.product_name)
    actionsRaw.push(
      language === 'fr'
        ? `Produit ${label}: ${p.why}`
        : `Product ${label}: ${p.why}`,
    )
  }

  for (const w of recommendation?.safety_warnings ?? []) {
    const related = formatProductLabel(w.related_product_sku, w.related_product_name)
    risksRaw.push(`${w.severity}: ${w.message}${w.related_product_sku || w.related_product_name ? ` (${related})` : ''}`)
    rxDeltaRaw.push(`${w.severity}: ${w.message}`)
  }

  for (const q of recommendation?.follow_up_questions ?? []) {
    questionsRaw.push(q.question)
  }

  if (run?.input?.visit_ref) {
    deltaRaw.push(
      language === 'fr'
        ? `Analyse basee sur la visite ${run.input.visit_ref}.`
        : `Analysis based on visit ${run.input.visit_ref}.`,
    )
  }

  for (const event of run?.artifacts?.trace?.events ?? []) {
    if (event.type === 'tool_result' && typeof event.result_summary === 'string' && event.result_summary.trim()) {
      deltaRaw.push(event.result_summary.trim())
    } else if (typeof event.message === 'string' && event.message.trim()) {
      deltaRaw.push(event.message.trim())
    }
  }

  const actions = topNUnique(actionsRaw, 3)
  const risks = topNUnique(risksRaw, 3)
  const questions = topNUnique(questionsRaw, 3)
  const delta = topNUnique(deltaRaw, 3)
  const rxDelta = topNUnique(rxDeltaRaw, 3)

  return {
    actions:
      actions.length > 0
        ? actions
        : [
            language === 'fr'
              ? 'Aucune action critique detectee. Consultez les recommandations detaillees.'
              : 'No critical action detected. See detailed recommendations.',
          ],
    risks:
      risks.length > 0
        ? risks
        : [language === 'fr' ? 'Aucun risque majeur detecte.' : 'No major risk detected.'],
    questions:
      questions.length > 0
        ? questions
        : [
            language === 'fr'
              ? 'Confirmer les symptomes et leur evolution avec le patient.'
              : 'Confirm symptom evolution with the patient.',
          ],
    delta:
      delta.length > 0
        ? delta
        : [language === 'fr' ? 'Aucun changement notable depuis la derniere analyse.' : 'No notable change since last analysis.'],
    rxDelta:
      rxDelta.length > 0
        ? rxDelta
        : [language === 'fr' ? 'Aucun delta Rx critique.' : 'No critical Rx delta.'],
  }
}

type DemoPreset = {
  id: string
  label: string
  note: string
  case_ref?: string
  patient_ref?: string
  visit_ref?: string
}

const DEMO_PRESETS: DemoPreset[] = [
  {
    id: 'baseline',
    label: 'Baseline OTC',
    note: 'Flow complet sans escalade, avec evidence et handout imprimable.',
    case_ref: 'case_000042',
  },
  {
    id: 'redflag',
    label: 'Red Flag',
    note: 'Escalade recommandee et blocage des suggestions produit.',
    case_ref: 'case_redflag_000101',
  },
  {
    id: 'lowinfo',
    label: 'Low Info',
    note: 'Questions de suivi puis re-run guide.',
    case_ref: 'case_lowinfo_000102',
  },
  {
    id: 'uploaded_rx',
    label: 'Upload RX',
    note: 'Upload ordonnance -> auto refresh patient en arriere-plan.',
    patient_ref: 'pt_000000',
  },
]

const DEMO_CHECKLIST = [
  'At-a-glance en moins de 30 secondes',
  'Guardrails PHI (upload + follow-up)',
  'Evidence refs sur recommandations OTC',
  'Auto-refresh patient sans clic manuel',
  'Trace redactee sans prompts/secrets',
]

function App() {
  const apiBase = useMemo(() => {
    return import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
  }, [])
  const apiKey = useMemo(() => {
    return import.meta.env.VITE_API_KEY ?? ''
  }, [])

  const [language, setLanguage] = useState<'fr' | 'en'>('fr')
  const [activeTab, setActiveTab] = useState<'run' | 'patients' | 'db'>('run')
  const [demoMode, setDemoMode] = useState(false)
  const [demoPresetId, setDemoPresetId] = useState<string>(DEMO_PRESETS[0].id)
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
  const [patientAnalysisStatus, setPatientAnalysisStatus] = useState<PatientAnalysisStatus | null>(null)
  const [patientInbox, setPatientInbox] = useState<PatientAnalysisStatus[]>([])
  const [isRefreshingPatient, setIsRefreshingPatient] = useState(false)
  const [isPatientsLoading, setIsPatientsLoading] = useState(false)
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
  const prescriptionInputRef = useRef<HTMLInputElement | null>(null)
  const selectedPatientRef = useRef<string | null>(null)
  const openPatientRequestRef = useRef(0)

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
  const plannerPlan = run?.artifacts?.plan
  const activeDemoPreset = useMemo(
    () => DEMO_PRESETS.find((p) => p.id === demoPresetId) ?? DEMO_PRESETS[0],
    [demoPresetId],
  )
  const atAGlance = useMemo(() => buildAtAGlance(run, runLanguage), [run, runLanguage])
  const visibleAnalysisStatus = useMemo(() => {
    if (!selectedPatient) return null
    if (!patientAnalysisStatus) return null
    if (patientAnalysisStatus.patient_ref !== selectedPatient.patient_ref) return null
    return patientAnalysisStatus
  }, [patientAnalysisStatus, selectedPatient])

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

  useEffect(() => {
    selectedPatientRef.current = selectedPatient?.patient_ref ?? null
  }, [selectedPatient?.patient_ref])

  function setFollowUpAnswer(questionId: string, value: string) {
    setFollowUpAnswers((prev) => ({ ...prev, [questionId]: value }))
  }

  function clearPrescriptionSelection() {
    if (prescriptionInputRef.current) {
      prescriptionInputRef.current.value = ''
    }
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

  async function runDemoPreset(preset: DemoPreset) {
    setError(null)
    if (preset.visit_ref && preset.patient_ref) {
      setActiveTab('run')
      await startRun({ patient_ref: preset.patient_ref, visit_ref: preset.visit_ref })
      return
    }

    if (preset.patient_ref && !preset.case_ref) {
      setActiveTab('patients')
      setPatientQuery(preset.patient_ref)
      await openPatient(preset.patient_ref)
      return
    }

    setActiveTab('run')
    if (preset.case_ref) setCaseRef(preset.case_ref)
    await startRun({ case_ref: preset.case_ref ?? caseRef })
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
    const requestId = openPatientRequestRef.current + 1
    openPatientRequestRef.current = requestId
    if (selectedPatientRef.current !== patientRef) {
      setPatientAnalysisStatus(null)
    }
    if (!opts?.preserveUpload) {
      setUploadReceipt(null)
      clearPrescriptionSelection()
    }
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
      if (requestId !== openPatientRequestRef.current) return
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
      if (requestId !== openPatientRequestRef.current) return
      selectedPatientRef.current = p.patient_ref
      setSelectedPatient(p)
      setPatientVisits(Array.isArray(v.visits) ? v.visits : [])
      await Promise.all([
        loadPatientAnalysisStatus(patientRef),
        loadPatientInbox(),
      ])
    } catch {
      if (requestId === openPatientRequestRef.current) {
        setError(`Cannot reach API at ${apiBase}.`)
      }
    } finally {
      if (requestId === openPatientRequestRef.current) {
        setIsPatientsLoading(false)
      }
    }
  }

  async function loadPatientAnalysisStatus(patientRef: string) {
    try {
      const resp = await fetch(
        `${apiBase}/patients/${encodeURIComponent(patientRef)}/analysis-status`,
        { headers: buildApiHeaders() },
      )
      if (!resp.ok) return
      const payload = (await resp.json()) as PatientAnalysisStatus
      if (payload.patient_ref !== patientRef) return
      if (selectedPatientRef.current !== patientRef) return
      setPatientAnalysisStatus(payload)
    } catch {
      // ignore polling failures
    }
  }

  async function loadPatientInbox() {
    try {
      const resp = await fetch(`${apiBase}/patients/inbox?limit=50`, {
        headers: buildApiHeaders(),
      })
      if (!resp.ok) return
      const payload = (await resp.json()) as PatientInboxPayload
      setPatientInbox(Array.isArray(payload.patients) ? payload.patients : [])
    } catch {
      // ignore polling failures
    }
  }

  async function refreshSelectedPatient() {
    if (!selectedPatient) return
    setIsRefreshingPatient(true)
    setError(null)
    try {
      const resp = await fetch(
        `${apiBase}/patients/${encodeURIComponent(selectedPatient.patient_ref)}/refresh`,
        {
          method: 'POST',
          headers: buildApiHeaders({ json: true }),
          body: JSON.stringify({ reason: 'manual_refresh_ui' }),
        },
      )
      if (!resp.ok) {
        setError(`Failed to refresh patient analysis (${resp.status}).`)
        return
      }
      const payload = (await resp.json()) as { analysis_status?: PatientAnalysisStatus }
      const nextStatus = payload.analysis_status
      if (
        nextStatus &&
        nextStatus.patient_ref === selectedPatient.patient_ref &&
        selectedPatientRef.current === selectedPatient.patient_ref
      ) {
        setPatientAnalysisStatus(nextStatus)
      }
      await loadPatientInbox()
    } catch {
      setError(`Cannot reach API at ${apiBase}.`)
    } finally {
      setIsRefreshingPatient(false)
    }
  }

  async function startRunFromVisit(patientRef: string, visitRef: string) {
    setActiveTab('run')
    await startRun({ patient_ref: patientRef, visit_ref: visitRef })
  }

  async function uploadPrescriptionForSelectedPatient() {
    if (!selectedPatient) return
    const patientRefAtStart = selectedPatient.patient_ref
    const selectedFile = prescriptionInputRef.current?.files?.[0] ?? null
    if (!selectedFile) {
      setError('Select a PDF file first.')
      return
    }
    setError(null)
    setIsUploadingPrescription(true)
    try {
      const form = new FormData()
      form.append('patient_ref', patientRefAtStart)
      form.append('language', language)
      form.append('file', selectedFile)

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
      if (selectedPatientRef.current !== patientRefAtStart) {
        await loadPatientInbox()
        return
      }
      setUploadReceipt(receipt)
      clearPrescriptionSelection()
      await openPatient(patientRefAtStart, { preserveUpload: true })
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

  useEffect(() => {
    if (activeTab !== 'patients') return

    void loadPatientInbox()
    if (selectedPatient?.patient_ref) {
      void loadPatientAnalysisStatus(selectedPatient.patient_ref)
    }

    const interval = window.setInterval(() => {
      void loadPatientInbox()
      if (selectedPatient?.patient_ref) {
        void loadPatientAnalysisStatus(selectedPatient.patient_ref)
      }
    }, 3000)

    return () => {
      window.clearInterval(interval)
    }
    // Intentional polling while patient tab is active.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, selectedPatient?.patient_ref])

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
            <label>
              Demo
              <input
                type="checkbox"
                checked={demoMode}
                onChange={(e) => setDemoMode(e.target.checked)}
                data-testid="demo-mode-toggle"
              />
            </label>
            {activeTab === 'run' ? (
              <>
                {demoMode ? (
                  <>
                    <label>
                      Preset
                      <select
                        value={demoPresetId}
                        onChange={(e) => setDemoPresetId(e.target.value)}
                        data-testid="demo-preset-select"
                      >
                        {DEMO_PRESETS.map((preset) => (
                          <option key={preset.id} value={preset.id}>
                            {preset.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button
                      onClick={() => void runDemoPreset(activeDemoPreset)}
                      disabled={isStarting}
                      data-testid="start-run"
                    >
                      {isStarting ? 'Starting...' : `Run ${activeDemoPreset.label}`}
                    </button>
                  </>
                ) : (
                  <>
                    <label>
                      Case
                      <input value={caseRef} onChange={(e) => setCaseRef(e.target.value)} />
                    </label>
                    <button onClick={() => void startRun()} disabled={isStarting} data-testid="start-run">
                      {isStarting ? 'Starting...' : 'Start run'}
                    </button>
                  </>
                )}
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
              <div className="subTitle">Inbox: new changes since last analysis</div>
              <div className="patientsList" data-testid="patient-inbox">
                {patientInbox.length === 0 ? (
                  <div className="muted small">No pending changes.</div>
                ) : (
                  patientInbox.slice(0, 6).map((item) => (
                    <div key={`inbox-${item.patient_ref}`} className="qCard">
                      <div className="qHeader">
                        <div className="qText mono">{item.patient_ref}</div>
                        <span className={`sev ${item.status === 'failed' ? 'sevBlocker' : 'sevWarn'}`}>
                          {item.status}
                        </span>
                      </div>
                      <div className="muted small">
                        {item.latest_visit_ref ?? '—'} · {item.latest_visit_at ?? '—'}
                      </div>
                    </div>
                  ))
                )}
              </div>

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
                    <div>
                      <span className="k">analysis</span>
                      <span
                        className={`v statusBadge status-${visibleAnalysisStatus?.status ?? 'loading'}`}
                        data-testid="analysis-status"
                      >
                        {visibleAnalysisStatus?.status ?? 'loading'}
                      </span>
                    </div>
                  </div>
                  <div className="row">
                    <div className="muted small" data-testid="analysis-status-message">
                      {visibleAnalysisStatus?.message ?? 'Loading analysis status…'}
                    </div>
                    <button
                      className="printBtn"
                      onClick={() => void refreshSelectedPatient()}
                      disabled={isRefreshingPatient}
                      data-testid="patient-refresh-btn"
                    >
                      {isRefreshingPatient ? 'Refreshing…' : 'Refresh now'}
                    </button>
                  </div>

                  <div className="subTitle">Scan ordonnance (PDF text-layer)</div>
                  <div className="row">
                    <input
                      key={selectedPatient.patient_ref}
                      ref={prescriptionInputRef}
                      type="file"
                      accept="application/pdf,.pdf"
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
              <div className="muted small">
                Tip: use table <span className="mono">inventory</span> to inspect medication names and stock.
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
            <section className="panel heroPanel" data-testid="at-a-glance-panel">
              <div className="panelTitleRow">
                <div className="panelTitle">{runLanguage === 'fr' ? "Pre-brief 'At a glance'" : "Pre-brief 'At a glance'"}</div>
                <div className="muted small">{runLanguage === 'fr' ? 'Lecture rapide <30s' : 'Quick read <30s'}</div>
              </div>
              <div className="atGrid">
                <div className="glanceCard">
                  <div className="glanceLabel">{runLanguage === 'fr' ? 'Top 3 actions' : 'Top 3 actions'}</div>
                  <ul className="glanceList">
                    {atAGlance.actions.map((item, idx) => (
                      <li key={`action-${idx}`} className="glanceBullet">
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
                <div className="glanceCard">
                  <div className="glanceLabel">{runLanguage === 'fr' ? 'Top 3 risques / alertes' : 'Top 3 risks / warnings'}</div>
                  <ul className="glanceList">
                    {atAGlance.risks.map((item, idx) => (
                      <li key={`risk-${idx}`} className="glanceBullet">
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
                <div className="glanceCard">
                  <div className="glanceLabel">{runLanguage === 'fr' ? 'Top 3 questions a poser' : 'Top 3 questions to ask'}</div>
                  <ul className="glanceList">
                    {atAGlance.questions.map((item, idx) => (
                      <li key={`question-${idx}`} className="glanceBullet">
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
                <div className="glanceCard">
                  <div className="glanceLabel">{runLanguage === 'fr' ? "Ce qui a change depuis l'analyse precedente" : 'What changed since last analysis'}</div>
                  <ul className="glanceList">
                    {atAGlance.delta.map((item, idx) => (
                      <li key={`delta-${idx}`} className="glanceBullet">
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
                <div className="glanceCard">
                  <div className="glanceLabel">{runLanguage === 'fr' ? 'New Rx delta' : 'New Rx delta'}</div>
                  <ul className="glanceList">
                    {atAGlance.rxDelta.map((item, idx) => (
                      <li key={`rxdelta-${idx}`} className="glanceBullet">
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </section>

            {demoMode ? (
              <section className="panel">
                <div className="panelTitleRow">
                  <div className="panelTitle">Demo mode</div>
                  <span className="badge">{activeDemoPreset.label}</span>
                </div>
                <div className="muted small">{activeDemoPreset.note}</div>
                <div className="subTitle">What judges should notice</div>
                <ul className="glanceList" data-testid="demo-checklist">
                  {DEMO_CHECKLIST.map((item, idx) => (
                    <li key={`check-${idx}`} className="glanceBullet">
                      {item}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

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
                    <span className={`v statusBadge status-${run.status}`} data-testid="run-status">
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
                            {w.related_product_sku || w.related_product_name
                              ? ` (${formatProductLabel(w.related_product_sku, w.related_product_name)})`
                              : ''}
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
                            <span>{formatProductLabel(p.product_sku, p.product_name)}</span>
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

            <section className="panel" data-testid="planner-panel">
              <div className="panelTitle">Plan card</div>
              {!plannerPlan ? (
                <div className="muted small">Planner disabled or not available for this run.</div>
              ) : (
                <>
                  <div className="kv">
                    <div>
                      <span className="k">mode</span>
                      <span className="v mono">{plannerPlan.mode}</span>
                    </div>
                    <div>
                      <span className="k">fallback</span>
                      <span className="v">{plannerPlan.fallback_used ? 'yes' : 'no'}</span>
                    </div>
                    <div>
                      <span className="k">generated_at</span>
                      <span className="v mono">{plannerPlan.generated_at}</span>
                    </div>
                  </div>
                  <div className="subTitle">Safety checks</div>
                  <ul className="glanceList">
                    {plannerPlan.safety_checks.map((item, idx) => (
                      <li key={`plan-safe-${idx}`} className="glanceBullet">
                        {item}
                      </li>
                    ))}
                  </ul>
                  <div className="subTitle">Steps</div>
                  <div className="patientsList">
                    {plannerPlan.steps.map((step) => (
                      <div key={step.step_id} className="qCard">
                        <div className="qHeader">
                          <div className="qText">{step.title}</div>
                          <span className="badge">{step.kind}</span>
                        </div>
                        <div className="qReason">{step.detail}</div>
                        {step.evidence_refs.length > 0 ? (
                          <div className="muted small mono">{step.evidence_refs.join(', ')}</div>
                        ) : null}
                      </div>
                    ))}
                  </div>
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
                <MarkdownArticle className="markdownArticle artifactReport" markdown={run?.artifacts?.report_markdown} />
              </div>
              <div className="artifact artifactHandout">
                <div className="artifactTitle">Handout</div>
                <MarkdownArticle className="markdownArticle" markdown={run?.artifacts?.handout_markdown} />
              </div>
            </section>
          </main>
        )}
      </div>
    </>
  )
}

export default App
