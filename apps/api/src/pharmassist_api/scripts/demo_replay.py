from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from pharmassist_api import db, orchestrator


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def _run_demo_replay(out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    db.init_db()

    scenarios = [
        {
            "name": "normal",
            "case_ref": "case_000042",
            "language": "en",
            "follow_up_answers": None,
        },
        {
            "name": "redflag",
            "case_ref": "case_redflag_000101",
            "language": "en",
            "follow_up_answers": None,
        },
        {
            "name": "lowinfo_completed",
            "case_ref": "case_lowinfo_000102",
            "language": "en",
            "follow_up_answers": [
                {"question_id": "q_primary_domain", "answer": "digestive"},
                {"question_id": "q_overall_severity", "answer": "mild"},
                {"question_id": "q_fever", "answer": "no"},
                {"question_id": "q_breathing", "answer": "no"},
                {"question_id": "q_chest_pain", "answer": "no"},
            ],
        },
    ]

    outputs: list[dict[str, Any]] = []
    for sc in scenarios:
        if sc["follow_up_answers"]:
            run = orchestrator.new_run_with_answers(
                case_ref=str(sc["case_ref"]),
                language=str(sc["language"]),  # type: ignore[arg-type]
                trigger="manual",
                follow_up_answers=sc["follow_up_answers"],  # type: ignore[arg-type]
            )
        else:
            run = orchestrator.new_run(
                case_ref=str(sc["case_ref"]),
                language=str(sc["language"]),  # type: ignore[arg-type]
                trigger="manual",
            )

        await orchestrator.run_pipeline(run["run_id"])
        stored = db.get_run(run["run_id"])
        if not stored:
            raise RuntimeError(f"Run not found: {run['run_id']}")
        events = db.list_events(run["run_id"])

        run_path = out_dir / f"{sc['name']}_run.json"
        events_path = out_dir / f"{sc['name']}_events.json"
        _write_json(run_path, stored)
        _write_json(events_path, events)
        outputs.append(
            {
                "name": sc["name"],
                "run_id": stored["run_id"],
                "status": stored["status"],
                "case_ref": sc["case_ref"],
                "run_path": str(run_path),
                "events_path": str(events_path),
                "events_count": len(events),
            }
        )

    summary = {
        "schema_version": "0.0.0",
        "scenarios": outputs,
    }
    _write_json(out_dir / "summary.json", summary)

    md_lines = [
        "# Demo Replay Bundle",
        "",
        "Reproducible no-GPU bundle generated from local deterministic pipeline runs.",
        "",
        "| scenario | case_ref | run_id | status | events |",
        "|---|---|---|---|---:|",
    ]
    for row in outputs:
        md_lines.append(
            f"| {row['name']} | {row['case_ref']} | {row['run_id']} "
            f"| {row['status']} | {row['events_count']} |"
        )
    (out_dir / "summary.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic demo replay artifacts.")
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory for replay artifacts.",
    )
    args = parser.parse_args(argv)

    summary = asyncio.run(_run_demo_replay(args.out))
    print("DEMO_REPLAY_OK", f"scenarios={len(summary['scenarios'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
