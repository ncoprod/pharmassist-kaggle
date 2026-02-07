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
