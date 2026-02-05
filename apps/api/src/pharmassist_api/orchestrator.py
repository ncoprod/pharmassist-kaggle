from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from . import db
from .cases.load_case import load_case_bundle
from .contracts.validate_schema import validate_instance
from .privacy.phi_boundary import PhiBoundaryError, raise_if_phi
from .steps.a1_intake_extraction import extract_intake
from .steps.a3_triage import triage_and_followup
from .steps.a4_evidence_retrieval import retrieve_evidence
from .steps.a5_safety import compute_safety_warnings
from .steps.a6_product_ranker import rank_products
from .steps.a7_report_composer import compose_report_markdown
from .steps.a8_handout import compose_handout_markdown

SCHEMA_VERSION = "0.0.0"

# Day 3: stubbed pipeline. Day 4+ will implement real steps behind these names.
PIPELINE_STEPS = [
    "A2_phi_scrubber",
    "A1_intake_extraction",
    "A3_triage",
    "A6_product_ranker",
    "A5_safety",
    "A4_evidence_retrieval",
    "A7_report_composer",
    "A8_handout",
    "A9_trace",
]

# In-memory per-run queues for SSE. Assumes a single-process server (OK for Kaggle demo).
_RUN_QUEUES: dict[str, asyncio.Queue[dict[str, Any]]] = {}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def get_queue(run_id: str) -> asyncio.Queue[dict[str, Any]]:
    q = _RUN_QUEUES.get(run_id)
    if q is None:
        q = asyncio.Queue()
        _RUN_QUEUES[run_id] = q
    return q


def _publish(run_id: str, *, event_id: int, data: dict[str, Any]) -> None:
    # Non-blocking publish; SSE loop will drain.
    q = get_queue(run_id)
    q.put_nowait({"id": event_id, "data": data})


def emit_event(run_id: str, event_type: str, payload: dict[str, Any]) -> int:
    ts = payload.get("ts") or _now_iso()
    event_payload = {**payload, "ts": ts, "type": event_type}
    event_id = db.insert_event(run_id, event_type, event_payload)
    _publish(run_id, event_id=event_id, data=event_payload)
    return event_id


def new_run(*, case_ref: str, language: str, trigger: str) -> dict[str, Any]:
    return new_run_with_answers(
        case_ref=case_ref,
        language=language,
        trigger=trigger,
        follow_up_answers=None,
    )


def new_run_with_answers(
    *,
    case_ref: str,
    patient_ref: str | None = None,
    visit_ref: str | None = None,
    language: str,
    trigger: str,
    follow_up_answers: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    created_at = _now_iso()

    run = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": created_at,
        "status": "created",
        "input": {
            "case_ref": case_ref,
            **({"patient_ref": patient_ref} if patient_ref else {}),
            **({"visit_ref": visit_ref} if visit_ref else {}),
            "language": language,
            "trigger": trigger,
            **({"follow_up_answers": follow_up_answers} if follow_up_answers else {}),
        },
        "artifacts": {},
        "policy_violations": [],
    }
    validate_instance(run, "run")
    db.create_run(run)
    return run


async def run_pipeline(run_id: str) -> None:
    """Execute the stubbed pipeline and emit SSE-friendly events."""
    run = db.get_run(run_id)
    if not run:
        return

    prefilled_intake_extracted: dict[str, Any] | None = None

    visit_ref = run.get("input", {}).get("visit_ref")
    if isinstance(visit_ref, str) and visit_ref.strip():
        # Resolve run from synthetic pharmacy dataset (no OCR/prompt text persisted).
        visit = db.get_visit(visit_ref.strip())
        if not visit:
            db.update_run(run_id, status="failed_safe", policy_violations=[])
            emit_event(
                run_id,
                "finalized",
                {"message": "Run failed_safe (unknown visit_ref).", "ts": _now_iso()},
            )
            _RUN_QUEUES.pop(run_id, None)
            return

        patient_ref = run.get("input", {}).get("patient_ref") or visit.get("patient_ref")
        if not isinstance(patient_ref, str) or not patient_ref.strip():
            db.update_run(run_id, status="failed_safe", policy_violations=[])
            emit_event(
                run_id,
                "finalized",
                {"message": "Run failed_safe (missing patient_ref).", "ts": _now_iso()},
            )
            _RUN_QUEUES.pop(run_id, None)
            return

        patient = db.get_patient(patient_ref.strip())
        if not patient:
            db.update_run(run_id, status="failed_safe", policy_violations=[])
            emit_event(
                run_id,
                "finalized",
                {"message": "Run failed_safe (unknown patient_ref).", "ts": _now_iso()},
            )
            _RUN_QUEUES.pop(run_id, None)
            return

        bundle = {
            "llm_context": patient.get("llm_context") or {},
            "products": db.list_inventory(),
        }
        maybe_intake = visit.get("intake_extracted")
        if isinstance(maybe_intake, dict):
            prefilled_intake_extracted = maybe_intake
    else:
        # Load synthetic case bundle (Kaggle demo). Never persist raw OCR text in DB/events.
        try:
            bundle = load_case_bundle(run["input"]["case_ref"])
        except Exception:
            db.update_run(run_id, status="failed_safe", policy_violations=[])
            emit_event(
                run_id,
                "finalized",
                {
                    "message": "Run failed_safe: unknown case_ref (synthetic demo).",
                    "ts": _now_iso(),
                },
            )
            _RUN_QUEUES.pop(run_id, None)
            return

    language = run["input"]["language"]
    ocr_text = (bundle.get("intake_text_ocr") or {}).get(language) or ""
    if isinstance(ocr_text, str):
        ocr_sha = sha256(ocr_text.encode("utf-8")).hexdigest()[:12]
    else:
        ocr_sha = "na"
    ocr_len = len(ocr_text) if isinstance(ocr_text, str) else 0

    db.update_run(run_id, status="running")
    emit_event(
        run_id,
        "step_started",
        {"step": "pipeline", "message": "Run started (synthetic demo).", "ts": _now_iso()},
    )

    artifacts: dict[str, Any] = {}
    intake_extracted: dict[str, Any] | None = None
    recommendation: dict[str, Any] | None = None
    follow_up_answers = run.get("input", {}).get("follow_up_answers")

    for step in PIPELINE_STEPS:
        emit_event(
            run_id,
            "step_started",
            {"step": step, "message": f"Starting {step}.", "ts": _now_iso()},
        )

        if step == "A2_phi_scrubber":
            # Hard-stop PHI boundary (defense-in-depth).
            try:
                raise_if_phi(str(ocr_text), "$.intake_text_ocr")
            except PhiBoundaryError as e:
                violations = [
                    {
                        "code": v.code,
                        "severity": v.severity,
                        "json_path": v.json_path,
                        "message": v.message,
                    }
                    for v in e.violations
                ]
                db.update_run(run_id, status="failed_safe", policy_violations=violations)
                emit_event(
                    run_id,
                    "policy_violation",
                    {
                        "step": step,
                        "message": "PHI boundary triggered; stopping safely.",
                        "ocr_len": ocr_len,
                        "ocr_sha256_12": ocr_sha,
                        "violations": violations,
                        "ts": _now_iso(),
                    },
                )
                emit_event(
                    run_id,
                    "finalized",
                    {"message": "Run failed_safe (PHI detected).", "ts": _now_iso()},
                )
                _RUN_QUEUES.pop(run_id, None)
                return

            await asyncio.sleep(0.1)

        elif step == "A1_intake_extraction":
            if isinstance(prefilled_intake_extracted, dict):
                # Visit-based runs provide a pre-extracted structured intake (no OCR text).
                intake_extracted = dict(prefilled_intake_extracted)
                artifacts["intake_extracted"] = intake_extracted
                emit_event(
                    run_id,
                    "tool_result",
                    {
                        "step": step,
                        "tool_name": "intake_source",
                        "result_summary": "prefilled intake_extracted from visit_ref",
                    },
                )
                await asyncio.sleep(0.05)
                continue

            try:
                intake_extracted = extract_intake(str(ocr_text), language)
            except PhiBoundaryError as e:
                violations = [
                    {
                        "code": v.code,
                        "severity": v.severity,
                        "json_path": v.json_path,
                        "message": v.message,
                    }
                    for v in e.violations
                ]
                db.update_run(run_id, status="failed_safe", policy_violations=violations)
                emit_event(
                    run_id,
                    "policy_violation",
                    {
                        "step": step,
                        "message": "PHI boundary triggered; stopping safely.",
                        "ocr_len": ocr_len,
                        "ocr_sha256_12": ocr_sha,
                        "violations": violations,
                        "ts": _now_iso(),
                    },
                )
                emit_event(
                    run_id,
                    "finalized",
                    {"message": "Run failed_safe (PHI detected).", "ts": _now_iso()},
                )
                _RUN_QUEUES.pop(run_id, None)
                return
            except Exception:
                # Fail safe: do not leak inputs, and make sure the run finalizes cleanly.
                db.update_run(run_id, status="failed_safe", policy_violations=[])
                emit_event(
                    run_id,
                    "finalized",
                    {"message": "Run failed_safe (A1 error).", "ts": _now_iso()},
                )
                _RUN_QUEUES.pop(run_id, None)
                return
            await asyncio.sleep(0.1)
            artifacts["intake_extracted"] = intake_extracted

        elif step == "A3_triage":
            if not isinstance(intake_extracted, dict):
                db.update_run(run_id, status="failed_safe", policy_violations=[])
                emit_event(
                    run_id,
                    "finalized",
                    {"message": "Run failed_safe (missing intake_extracted).", "ts": _now_iso()},
                )
                _RUN_QUEUES.pop(run_id, None)
                return

            intake_extracted, recommendation, needs_more_info, triage_meta = triage_and_followup(
                intake_extracted=intake_extracted,
                llm_context=bundle.get("llm_context") or {},
                follow_up_answers=(
                    follow_up_answers if isinstance(follow_up_answers, list) else None
                ),
                language=language,
            )
            artifacts["intake_extracted"] = intake_extracted
            artifacts["recommendation"] = recommendation
            await asyncio.sleep(0.1)

            # Audit-friendly: follow-up selector (optional, safe metadata only).
            sel = None
            if isinstance(triage_meta, dict):
                maybe_sel = triage_meta.get("followup_selector")
                if isinstance(maybe_sel, dict):
                    sel = maybe_sel

            if isinstance(sel, dict) and sel.get("attempted") is True:
                raw_candidate_ids = sel.get("candidate_ids")
                candidate_ids = raw_candidate_ids if isinstance(raw_candidate_ids, list) else []

                raw_selected_ids = sel.get("selected_ids")
                selected_ids = raw_selected_ids if isinstance(raw_selected_ids, list) else []
                emit_event(
                    run_id,
                    "tool_call",
                    {
                        "step": step,
                        "tool_name": "followup_selector",
                        "args_redacted": {
                            "max_k": sel.get("max_k", 5),
                            "candidate_count": len(candidate_ids),
                            "candidate_ids": candidate_ids,
                        },
                    },
                )
                emit_event(
                    run_id,
                    "tool_result",
                    {
                        "step": step,
                        "tool_name": "followup_selector",
                        "result_summary": (
                            f"mode={sel.get('mode')} selected={len(selected_ids)} "
                            f"ids={','.join([str(x) for x in selected_ids])}"
                        )[:500],
                    },
                )

            # Audit-friendly events: which rules fired?
            for rf in intake_extracted.get("red_flags") or []:
                if isinstance(rf, str) and rf:
                    emit_event(
                        run_id,
                        "rule_fired",
                        {
                            "step": step,
                            "rule_id": rf,
                            "severity": "WARN",
                            "message": f"Red flag detected: {rf}",
                        },
                    )

            if needs_more_info:
                emit_event(
                    run_id,
                    "rule_fired",
                    {
                        "step": step,
                        "rule_id": "FOLLOW_UP_REQUIRED",
                        "severity": "WARN",
                        "message": (
                            "Follow-up required "
                            f"({len(recommendation.get('follow_up_questions') or [])} questions)."
                        ),
                    },
                )
                artifacts["trace"] = _build_trace_artifact(run_id)
                db.update_run(
                    run_id,
                    status="needs_more_info",
                    artifacts=artifacts,
                    policy_violations=[],
                )
                emit_event(
                    run_id,
                    "finalized",
                    {"message": "Run needs_more_info (follow-up required).", "ts": _now_iso()},
                )
                _RUN_QUEUES.pop(run_id, None)
                return

            # If triage recommends escalation, stop early and do not recommend products.
            if (
                isinstance(recommendation, dict)
                and isinstance(recommendation.get("escalation"), dict)
                and recommendation["escalation"].get("recommended") is True
            ):
                esc = recommendation["escalation"]
                report_md = (
                    "# Pharmacist report (synthetic)\n\n"
                    "- Scope: OTC/parapharmacy decision support only.\n"
                    f"- Escalation: {esc.get('suggested_service')}.\n"
                    f"- Reason: {esc.get('reason')}.\n"
                )
                handout_md = (
                    "# Patient handout (synthetic)\n\n"
                    "- Suivez les consignes du pharmacien.\n"
                    f"- {esc.get('reason')}\n"
                )
                artifacts["report_markdown"] = report_md
                artifacts["handout_markdown"] = handout_md
                artifacts["trace"] = _build_trace_artifact(run_id)

                db.update_run(
                    run_id,
                    status="completed",
                    artifacts=artifacts,
                    policy_violations=[],
                )
                emit_event(
                    run_id,
                    "finalized",
                    {"message": "Run completed (escalation recommended).", "ts": _now_iso()},
                )
                _RUN_QUEUES.pop(run_id, None)
                return

        elif step == "A6_product_ranker":
            if not isinstance(intake_extracted, dict) or not isinstance(recommendation, dict):
                db.update_run(run_id, status="failed_safe", policy_violations=[])
                emit_event(
                    run_id,
                    "finalized",
                    {"message": "Run failed_safe (missing A3 artifacts).", "ts": _now_iso()},
                )
                _RUN_QUEUES.pop(run_id, None)
                return

            ranked_products, ranker_warnings = rank_products(
                intake_extracted=intake_extracted,
                llm_context=bundle.get("llm_context") or {},
                follow_up_answers=(
                    follow_up_answers if isinstance(follow_up_answers, list) else None
                ),
                products=bundle.get("products") or [],
            )
            emit_event(
                run_id,
                "tool_result",
                {
                    "step": step,
                    "tool_name": "product_ranker",
                    "result_summary": (
                        f"ranked_products={len(ranked_products)} "
                        f"excluded_warnings={len(ranker_warnings or [])}"
                    ),
                },
            )
            recommendation = dict(recommendation)
            recommendation["ranked_products"] = ranked_products
            recommendation["safety_warnings"] = _dedupe_warnings(
                list(recommendation.get("safety_warnings") or []) + list(ranker_warnings or [])
            )
            artifacts["recommendation"] = recommendation
            await asyncio.sleep(0.1)

        elif step == "A5_safety":
            if not isinstance(recommendation, dict):
                db.update_run(run_id, status="failed_safe", policy_violations=[])
                emit_event(
                    run_id,
                    "finalized",
                    {"message": "Run failed_safe (missing recommendation).", "ts": _now_iso()},
                )
                _RUN_QUEUES.pop(run_id, None)
                return

            products_by_sku = {
                str(p.get("sku")): p
                for p in (bundle.get("products") or [])
                if isinstance(p, dict) and isinstance(p.get("sku"), str) and p.get("sku")
            }
            safety = compute_safety_warnings(
                llm_context=bundle.get("llm_context") or {},
                follow_up_answers=(
                    follow_up_answers if isinstance(follow_up_answers, list) else None
                ),
                products_by_sku=products_by_sku,
                ranked_products=list(recommendation.get("ranked_products") or []),
                escalation=recommendation.get("escalation")
                if isinstance(recommendation.get("escalation"), dict)
                else None,
            )
            emit_event(
                run_id,
                "tool_result",
                {
                    "step": step,
                    "tool_name": "safety_engine",
                    "result_summary": f"safety_warnings_added={len(safety or [])}",
                },
            )
            recommendation = dict(recommendation)
            recommendation["safety_warnings"] = _dedupe_warnings(
                list(recommendation.get("safety_warnings") or []) + list(safety or [])
            )
            artifacts["recommendation"] = recommendation
            await asyncio.sleep(0.1)

        elif step == "A4_evidence_retrieval":
            if not isinstance(intake_extracted, dict):
                db.update_run(run_id, status="failed_safe", policy_violations=[])
                emit_event(
                    run_id,
                    "finalized",
                    {"message": "Run failed_safe (missing intake_extracted).", "ts": _now_iso()},
                )
                _RUN_QUEUES.pop(run_id, None)
                return

            emit_event(
                run_id,
                "tool_call",
                {"step": step, "tool_name": "evidence_retrieval", "args_redacted": {"k": 5}},
            )
            evidence_items = retrieve_evidence(
                intake_extracted=intake_extracted,
                llm_context=bundle.get("llm_context") or {},
                k=5,
            )
            emit_event(
                run_id,
                "tool_result",
                {
                    "step": step,
                    "tool_name": "evidence_retrieval",
                    "result_summary": f"retrieved={len(evidence_items)}",
                },
            )
            artifacts["evidence_items"] = evidence_items

            # Attach safe evidence_refs to ranked products (ids only).
            evidence_ids = [
                str(e.get("evidence_id"))
                for e in evidence_items
                if isinstance(e, dict) and isinstance(e.get("evidence_id"), str)
            ]
            if isinstance(recommendation, dict):
                rec = dict(recommendation)
                ranked = []
                for rp in rec.get("ranked_products") or []:
                    if not isinstance(rp, dict):
                        continue
                    refs = evidence_ids[:2]
                    ranked.append({**rp, "evidence_refs": refs})
                rec["ranked_products"] = ranked
                recommendation = rec
                artifacts["recommendation"] = recommendation

            await asyncio.sleep(0.1)

        elif step == "A7_report_composer":
            if not isinstance(intake_extracted, dict):
                db.update_run(run_id, status="failed_safe", policy_violations=[])
                emit_event(
                    run_id,
                    "finalized",
                    {"message": "Run failed_safe (missing intake_extracted).", "ts": _now_iso()},
                )
                _RUN_QUEUES.pop(run_id, None)
                return

            report_md = compose_report_markdown(
                intake_extracted=intake_extracted,
                recommendation=recommendation if isinstance(recommendation, dict) else None,
                evidence_items=artifacts.get("evidence_items")
                if isinstance(artifacts.get("evidence_items"), list)
                else None,
                language=language,
            )
            artifacts["report_markdown"] = report_md
            await asyncio.sleep(0.1)

        elif step == "A8_handout":
            handout_md = compose_handout_markdown(
                recommendation=recommendation if isinstance(recommendation, dict) else None,
                language=language,
            )
            artifacts["handout_markdown"] = handout_md
            await asyncio.sleep(0.1)

        else:
            # Simulate work; keeps the UI feeling alive without heavy computation.
            await asyncio.sleep(0.25)

        emit_event(
            run_id,
            "step_completed",
            {"step": step, "message": f"Completed {step}.", "ts": _now_iso()},
        )

    # Fallback placeholder artifacts (only if a step didn't set them).
    symptoms_line = ""
    if isinstance(intake_extracted, dict):
        labels = []
        for s in intake_extracted.get("symptoms") or []:
            if isinstance(s, dict) and isinstance(s.get("label"), str):
                labels.append(s["label"])
        if labels:
            symptoms_line = "- Extracted symptoms: " + ", ".join(labels) + "\n"

    if "report_markdown" not in artifacts:
        artifacts["report_markdown"] = (
            "# Pharmacist report (synthetic)\n\n"
            "- Scope: OTC/parapharmacy decision support only.\n"
            f"{symptoms_line}"
            "- Note: Ne modifiez pas votre traitement sur ordonnance sans avis medical.\n"
        )
    if "handout_markdown" not in artifacts:
        artifacts["handout_markdown"] = (
            "# Patient handout (synthetic)\n\n"
            "- Suivez les conseils du pharmacien.\n"
            "- Si aggravation ou symptomes inhabituels: consultez un medecin.\n"
        )

    # Build a redacted trace artifact for audit/debugging (no prompts, no OCR text).
    artifacts["trace"] = _build_trace_artifact(run_id)

    db.update_run(run_id, status="completed", artifacts=artifacts, policy_violations=[])

    emit_event(
        run_id,
        "finalized",
        {"message": "Run completed (synthetic demo).", "ts": _now_iso()},
    )

    # Queue is only needed for live streaming; completed runs can replay from DB.
    _RUN_QUEUES.pop(run_id, None)


def dumps_sse(
    data: dict[str, Any], *, event_id: int | None = None, event: str | None = None
) -> str:
    """Serialize an SSE message."""
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    if event is not None:
        lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


def _dedupe_warnings(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for w in warnings:
        if not isinstance(w, dict):
            continue
        code = str(w.get("code") or "")
        sku = w.get("related_product_sku")
        sku_key = sku if isinstance(sku, str) else None
        key = (code, sku_key)
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
    return out


def _build_trace_artifact(run_id: str) -> dict[str, Any]:
    """Assemble a redacted trace artifact from stored events.

    The trace is contract-validated and intentionally excludes any raw OCR text or prompts.
    """
    events: list[dict[str, Any]] = []

    for item in db.list_events(run_id):
        data = item.get("data") or {}
        if not isinstance(data, dict):
            continue
        etype = data.get("type")
        ts = data.get("ts")
        if not isinstance(etype, str) or not etype:
            continue
        if not isinstance(ts, str) or not ts:
            ts = _now_iso()

        if etype == "policy_violation":
            # Current pipeline may emit a list of violations; convert to one event per violation.
            viols = data.get("violations")
            if isinstance(viols, list) and viols:
                for v in viols:
                    if not isinstance(v, dict):
                        continue
                    events.append(
                        {
                            "event_id": str(uuid.uuid4()),
                            "ts": ts,
                            "type": "policy_violation",
                            "violation": v,
                            "message": str(data.get("message") or "Policy violation."),
                        }
                    )
            continue

        ev: dict[str, Any] = {"event_id": str(uuid.uuid4()), "ts": ts, "type": etype}

        step = data.get("step")
        if isinstance(step, str) and step:
            ev["step"] = step
        msg = data.get("message")
        if isinstance(msg, str) and msg:
            ev["message"] = msg

        if etype in {"tool_call", "tool_result"}:
            tool_name = data.get("tool_name")
            if not isinstance(tool_name, str) or not tool_name:
                continue
            ev["tool_name"] = tool_name
            if etype == "tool_call":
                args_redacted = data.get("args_redacted")
                if isinstance(args_redacted, dict):
                    ev["args_redacted"] = args_redacted
            else:
                result_summary = data.get("result_summary")
                if isinstance(result_summary, str) and result_summary:
                    ev["result_summary"] = result_summary

        if etype == "rule_fired":
            rule_id = data.get("rule_id")
            if not isinstance(rule_id, str) or not rule_id:
                continue
            ev["rule_id"] = rule_id
            sev = data.get("severity")
            if isinstance(sev, str) and sev:
                ev["severity"] = sev

        events.append(ev)

    trace = {
        "schema_version": SCHEMA_VERSION,
        "trace_id": str(uuid.uuid4()),
        "run_id": run_id,
        "events": events,
    }

    # If anything doesn't validate, fall back to an empty trace.
    try:
        validate_instance(trace, "trace")
    except Exception:
        trace = {
            "schema_version": SCHEMA_VERSION,
            "trace_id": str(uuid.uuid4()),
            "run_id": run_id,
            "events": [],
        }

    return trace
