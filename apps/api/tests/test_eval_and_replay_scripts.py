from __future__ import annotations

import asyncio
from pathlib import Path


def test_eval_suite_writes_summary(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "eval.db"))

    from pharmassist_api.scripts import eval_suite

    out_dir = tmp_path / "eval_out"
    summary = asyncio.run(eval_suite._run_eval(out_dir))
    assert summary["total_cases"] >= 4
    assert 0.0 <= float(summary["red_flag_recall"]) <= 1.0
    assert (out_dir / "summary.json").exists()
    assert (out_dir / "summary.md").exists()


def test_demo_replay_writes_artifacts(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "replay.db"))

    from pharmassist_api.scripts import demo_replay

    out_dir = tmp_path / "replay_out"
    summary = asyncio.run(demo_replay._run_demo_replay(out_dir))
    scenarios = summary.get("scenarios") or []
    assert len(scenarios) == 3
    assert (out_dir / "summary.json").exists()
    assert (out_dir / "summary.md").exists()
    for row in scenarios:
        assert Path(str(row["run_path"])).exists()
        assert Path(str(row["events_path"])).exists()


def test_eval_suite_schema_valid_rate_uses_row_flag(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "eval_rate.db"))

    from pharmassist_api.scripts import eval_suite

    async def _fake_run_case(_case):
        return {
            "case_id": "fake",
            "case_ref": "case_000042",
            "language": "en",
            "run_id": "run_fake",
            "status": "completed",
            "schema_valid": False,
            "schema_error": "boom",
            "expected_escalation": False,
            "actual_escalation": False,
            "symptom_f1": 0.0,
            "latency_ms": 1.0,
        }

    monkeypatch.setattr(eval_suite, "_run_case", _fake_run_case)
    out_dir = tmp_path / "eval_out_rate"
    summary = asyncio.run(eval_suite._run_eval(out_dir))
    assert float(summary["schema_valid_rate"]) == 0.0


def test_eval_suite_main_fails_on_invalid_rows(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "eval_main_fail.db"))

    from pharmassist_api.scripts import eval_suite

    async def _fake_run_eval(_out_dir):
        return {
            "schema_version": "0.0.0",
            "total_cases": 1,
            "completed_cases": 1,
            "schema_valid_rate": 0.0,
            "red_flag_recall": 1.0,
            "mean_symptom_f1": 1.0,
            "p95_latency_ms": 1.0,
            "rows": [
                {
                    "case_id": "fake",
                    "status": "completed",
                    "schema_valid": False,
                }
            ],
        }

    monkeypatch.setattr(eval_suite, "_run_eval", _fake_run_eval)
    code = eval_suite.main(["--out", str(tmp_path / "eval_out_main_fail")])
    assert code == 1
