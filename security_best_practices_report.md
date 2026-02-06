## Executive Summary

Security hardening + red-team checks were run on `codex/feb07-admin-hardening-redteam`.
Current status: **no high/critical findings remaining** in app-code review.

Primary improvements delivered:
- API access controls on data endpoints (`/runs*`, `/patients*`).
- Admin endpoint hardening (`/admin/db-preview*`) with auth, loopback/proxy controls, rate limit, and audit logging.
- Dependency hardening for known Starlette CVEs.

## Scope

- Backend: FastAPI app (`apps/api/src/pharmassist_api/main.py`, `db.py`).
- Frontend: React app auth header wiring for API/admin calls (`apps/web/src/App.tsx`).
- Runtime checks: `scripts/redteam_check.sh`.
- Dependency and static checks: `pip-audit`, `bandit`, `npm audit`.

## Fixes Applied

1) Data endpoint access control
- Added `PHARMASSIST_API_KEY` support:
  - header: `X-Api-Key`
  - SSE fallback: query param `api_key`
- Without key:
  - endpoints are loopback-only
  - requests with proxy forward headers are rejected

2) Admin endpoint hardening
- Added `PHARMASSIST_ADMIN_API_KEY` support via `X-Admin-Key`.
- Without key:
  - loopback-only
  - requests with proxy forward headers are rejected.
- Added rate limiting:
  - `PHARMASSIST_ADMIN_RATE_LIMIT_MAX`
  - `PHARMASSIST_ADMIN_RATE_LIMIT_WINDOW_SEC`
- Added audit persistence table:
  - `admin_audit_events`
  - only redacted metadata (`query_len`, `query_sha256_12`, etc.)

3) Dependency hardening
- Upgraded API deps to remove Starlette advisories found by `pip-audit`:
  - `fastapi>=0.120,<0.121`
  - `starlette>=0.49.1,<0.50`

## Verification Evidence

1) Repo gates
- `make lint`
- `npm -w apps/web run build`
- `make validate`
- `.venv/bin/pytest -q`
- `make e2e`

2) Red-team runtime checks
- `./scripts/redteam_check.sh`
- Validated:
  - unauthorized admin request -> `401`
  - wrong admin key -> `401`
  - valid admin key -> `200`
  - SQLi-like table input -> `400`
  - admin rate limit -> `429`

3) Security scans
- `pip-audit`: no known vulnerabilities (excluding local editable package).
- `bandit -lll`: no high-severity Python findings.
- `npm audit --omit=dev --audit-level=high`: no vulnerabilities.

4) Focused code review (subagent)
- Result: no high/critical findings after fixes.

## Residual Risks (non-high)

- `api_key` query parameter is supported for SSE compatibility; query params can be captured by logs/proxies. Prefer `X-Api-Key` for non-SSE requests and restrict log exposure at edge.
- If a reverse proxy is misconfigured to strip forwarding headers and flatten source IP behavior, deployment policy must still enforce explicit API keys in production.

## Recommended Production Defaults

- Set both:
  - `PHARMASSIST_API_KEY`
  - `PHARMASSIST_ADMIN_API_KEY`
- Keep admin rate limit active.
- Keep admin/data endpoints behind TLS and trusted edge controls.
