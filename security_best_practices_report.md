# Security Best Practices Report - API TUSSAM

Date: 2026-02-22
Scope: FastAPI app, scheduler, Docker deployment, dependency posture, auth/rate-limit controls.

## Executive Summary
The API had several security-by-default gaps in admin endpoint protection and deployment defaults. These were remediated in this audit pass (fail-closed sync auth, weak key rejection, CORS tightening, docs gating, non-root container runtime, supported Python runtime, and packaging/dependency auditability fixes). Runtime dependency audit for application requirements returned no known CVEs.

## Critical / High Findings

### SBP-001 (High) - `/sync/*` authorization could fail open when `SYNC_API_KEY` was missing
Impact: Anyone with network access could trigger administrative sync operations if the key was unset.

Evidence and fix:
- Enforced fail-closed behavior in `verify_sync_key` (`503` when missing key): `app/main.py:55`
- Explicit insecure dev-only override added (`ALLOW_INSECURE_SYNC=true`): `app/main.py:59`
- Added tests for fail-closed and explicit insecure mode: `tests/test_main.py:470`, `tests/test_main.py:482`

Status: Resolved.

### SBP-002 (High) - Weak default sync key in container configuration
Impact: Predictable default key (`cambia-esta-clave`) is guessable, enabling unauthorized admin actions.

Evidence and fix:
- Removed weak fallback from compose env: `docker-compose.yml:17`
- Added explicit weak-key rejection guard in app code: `app/main.py:69`
- Added regression test for weak key rejection: `tests/test_main.py:494`

Status: Resolved.

### SBP-003 (High) - Base container runtime on unsupported Python version
Impact: Python 3.9 is out of security support in 2026, increasing exposure to unpatched runtime vulnerabilities.

Evidence and fix:
- Upgraded image from `python:3.9-slim` to `python:3.12-slim`: `Dockerfile:1`

Status: Resolved.

## Medium Findings

### SBP-004 (Medium) - Overly permissive CORS and always-on docs surface
Impact: Unnecessary API surface and browser cross-origin exposure increase reconnaissance and abuse opportunities.

Evidence and fix:
- Docs/OpenAPI now disabled by default and controlled via env: `app/main.py:177`, `app/main.py:182`
- CORS now disabled by default and only enabled by explicit allowlist env: `app/main.py:196`
- Security headers middleware added (`nosniff`, `DENY`, `no-referrer`): `app/main.py:150`

Status: Resolved.

## Low Findings / Residual Risk

### SBP-005 (Low) - Host header allowlist not enforced in app layer
Impact: If reverse-proxy host validation is missing, Host-header abuse risk depends on edge configuration.

Evidence:
- No `TrustedHostMiddleware` configured in app middleware block: `app/main.py:192`

Recommendation:
- Enforce host allowlisting at edge (preferred) and/or add `TrustedHostMiddleware` with explicit allowed hosts.

Status: Open (requires deployment-specific host list).

## Dependency Audit
- Runtime dependency audit (`pip-audit` against project requirements): no known vulnerabilities.
- Packaging issue that blocked `pip install -e .` was fixed by explicit package discovery config: `pyproject.toml:43`.

## Verification Performed
- Unit/integration (non-e2e) tests: `93 passed`.
- Security behavior tests added for sync auth edge cases: `tests/test_main.py:470`.
