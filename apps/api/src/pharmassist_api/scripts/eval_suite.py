from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pharmassist_api import db, orchestrator
from pharmassist_api.cases.load_case import load_case_bundle
from pharmassist_api.contracts.validate_schema import validate_instance


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    case_ref: str
    language: str
    expected_escalation: bool
    follow_up_answers: list[dict[str, str]] | None = None


CASES: tuple[EvalCase, ...] = (
    EvalCase(
        case_id="normal_en",
        case_ref="case_000042",
        language="en",
        expected_escalation=False,
    ),
    EvalCase(
        case_id="normal_fr",
        case_ref="case_000043",
        language="fr",
        expected_escalation=False,
    ),
    EvalCase(
        case_id="redflag_en",
        case_ref="case_redflag_000101",
        language="en",
        expected_escalation=True,
    ),
    EvalCase(
        case_id="lowinfo_with_answers",
        case_ref="case_lowinfo_000102",
        language="en",
        expected_escalation=False,
        follow_up_answers=[
            {"question_id": "q_primary_domain", "answer": "digestive"},
            {"question_id": "q_overall_severity", "answer": "mild"},
            {"question_id": "q_fever", "answer": "no"},
            {"question_id": "q_breathing", "answer": "no"},
            {"question_id": "q_chest_pain", "answer": "no"},
        ],
    ),
)


def _safe_label_set(intake_extracted: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for s in intake_extracted.get("symptoms") or []:
        if isinstance(s, dict) and isinstance(s.get("label"), str) and s["label"].strip():
            out.add(s["label"].strip().lower())
    return out


def _f1(pred: set[str], true: set[str]) -> float:
    if not pred and not true:
        return 1.0
    tp = len(pred.intersection(true))
    if tp == 0:
        return 0.0
    precision = tp / len(pred) if pred else 0.0
    recall = tp / len(true) if true else 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


async def _run_case(case: EvalCase) -> dict[str, Any]:
    if case.follow_up_answers:
        run = orchestrator.new_run_with_answers(
            case_ref=case.case_ref,
            language=case.language,  # type: ignore[arg-type]
            trigger="manual",
            follow_up_answers=case.follow_up_answers,
        )
    else:
        run = orchestrator.new_run(
            case_ref=case.case_ref,
            language=case.language,  # type: ignore[arg-type]
            trigger="manual",
        )
    started = time.perf_counter()
    await orchestrator.run_pipeline(run["run_id"])
    latency_ms = (time.perf_counter() - started) * 1000.0
    stored = db.get_run(run["run_id"])
    if not stored:
        raise RuntimeError(f"Run not found after execution: {run['run_id']}")
    validate_instance(stored, "run")

    escalation = (
        stored.get("artifacts", {})
        .get("recommendation", {})
        .get("escalation", {})
        .get("recommended", False)
    )
    escalation_bool = bool(escalation)

    expected_bundle = load_case_bundle(case.case_ref)
    expected_intake = expected_bundle.get("intake_extracted")
    expected_labels = _safe_label_set(expected_intake if isinstance(expected_intake, dict) else {})
    got_intake = stored.get("artifacts", {}).get("intake_extracted")
    got_labels = _safe_label_set(got_intake if isinstance(got_intake, dict) else {})

    return {
        "case_id": case.case_id,
        "case_ref": case.case_ref,
        "language": case.language,
        "run_id": stored["run_id"],
        "status": stored["status"],
        "expected_escalation": case.expected_escalation,
        "actual_escalation": escalation_bool,
        "symptom_f1": _f1(got_labels, expected_labels),
        "latency_ms": round(latency_ms, 2),
    }


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    idx = int(round(0.95 * (len(xs) - 1)))
    return xs[idx]


def _write_markdown(*, out_path: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    lines = [
        "# PharmAssist Eval Report",
        "",
        f"- total_cases: {summary['total_cases']}",
        f"- completed: {summary['completed_cases']}",
        f"- schema_valid_rate: {summary['schema_valid_rate']:.4f}",
        f"- red_flag_recall: {summary['red_flag_recall']:.4f}",
        f"- mean_symptom_f1: {summary['mean_symptom_f1']:.4f}",
        f"- p95_latency_ms: {summary['p95_latency_ms']:.2f}",
        "",
        "| case_id | status | escalation(expected/actual) | symptom_f1 | latency_ms |",
        "|---|---|---|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| "
            f"{row['case_id']} | {row['status']} | "
            f"{row['expected_escalation']}/{row['actual_escalation']} | "
            f"{row['symptom_f1']:.4f} | {row['latency_ms']:.2f} |"
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _run_eval(out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    db.init_db()

    rows: list[dict[str, Any]] = []
    for case in CASES:
        rows.append(await _run_case(case))

    completed = [r for r in rows if r["status"] == "completed"]
    schema_valid_rate = len(rows) / len(rows) if rows else 0.0
    redflag_expected = [r for r in rows if r["expected_escalation"]]
    redflag_tp = [r for r in redflag_expected if r["actual_escalation"]]
    red_flag_recall = (len(redflag_tp) / len(redflag_expected)) if redflag_expected else 1.0
    mean_symptom_f1 = statistics.fmean([float(r["symptom_f1"]) for r in rows]) if rows else 0.0
    p95_latency_ms = _p95([float(r["latency_ms"]) for r in rows])

    summary = {
        "schema_version": "0.0.0",
        "total_cases": len(rows),
        "completed_cases": len(completed),
        "schema_valid_rate": schema_valid_rate,
        "red_flag_recall": red_flag_recall,
        "mean_symptom_f1": mean_symptom_f1,
        "p95_latency_ms": p95_latency_ms,
        "rows": rows,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_markdown(out_path=out_dir / "summary.md", summary=summary, rows=rows)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run deterministic PharmAssist eval suite (no GPU)."
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory for eval artifacts.",
    )
    args = parser.parse_args(argv)

    summary = asyncio.run(_run_eval(args.out))
    print(
        "EVAL_OK",
        f"cases={summary['total_cases']}",
        f"red_flag_recall={summary['red_flag_recall']:.4f}",
        f"p95_latency_ms={summary['p95_latency_ms']:.2f}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
