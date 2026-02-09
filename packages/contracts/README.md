# Contracts (Schemas)

This folder hosts the **canonical JSON Schemas** shared across the demo:

Schemas live in `packages/contracts/schemas/` and examples live in
`packages/contracts/examples/`.

Key schemas:
- `llm_context.schema.json`: PHI-safe context allowlist for model calls
- `intake_extracted.schema.json`: structured extraction from untrusted OCR/noisy text
- `product.schema.json`: parapharmacy catalog items
- `recommendation.schema.json`: ranked products + safety warnings + follow-up questions
- `evidence_item.schema.json`: citations/provenance
- `trace.schema.json`: agentic trace (no raw prompts)
- `run.schema.json`: end-to-end run state + policy violations
- `prebrief.schema.json`: at-a-glance summary used by the judge-facing UI
- `planner_plan.schema.json`: feature-flagged planner artifact (agentic + deterministic fallback)
- `patient_analysis_status.schema.json`: refresh state for patient auto-analysis
- `patient_inbox.schema.json`: "new changes since last analysis" inbox payload
- `pharmacy_event_payload.schema.json`: strict allowlist payloads for synthetic pharmacy events
- `db_preview.schema.json`: redacted read-only admin preview payload
- `document_upload_receipt.schema.json`: metadata-only response for prescription PDF ingestion

Validation:

```bash
make validate
```

This validates every JSON file in `packages/contracts/examples/` against its schema.
