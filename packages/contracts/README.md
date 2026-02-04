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

Validation:

```bash
make validate
```

This validates every JSON file in `packages/contracts/examples/` against its schema.
