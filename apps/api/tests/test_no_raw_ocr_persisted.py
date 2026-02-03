import asyncio
import json


def test_events_do_not_persist_raw_ocr_text(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMASSIST_DB_PATH", str(tmp_path / "test.db"))

    from pharmassist_api import db
    from pharmassist_api.cases.load_case import load_case_bundle
    from pharmassist_api.orchestrator import new_run, run_pipeline

    db.init_db()

    bundle = load_case_bundle("case_000042")
    ocr_text = bundle["intake_text_ocr"]["en"]
    # Use a stable snippet that should never show up in stored events.
    needle = "PATIENT NOTE"
    assert needle in ocr_text

    run = new_run(case_ref="case_000042", language="en", trigger="manual")
    asyncio.run(run_pipeline(run["run_id"]))

    events = db.list_events(run["run_id"])
    blob = json.dumps([e["data"] for e in events], ensure_ascii=False)
    assert needle not in blob

