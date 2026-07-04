# ADR-006: Egress Controls — Railway Limitation and App-Level Compensating Control

**Date:** 2026-07-04
**Status:** Accepted
**Phase:** 05-seguridad-y-audit-log

---

## Context

PROJECT.md layer 10 requires that the bot restrict outbound network egress to five
authorized destinations: SoftSeguros + Meta Graph API + Chatwoot + OpenRouter +
LangSmith.  Sentry (error tracking) and the lambda-proyect internal service are
also intentional egress points.

The canonical implementation of network-level egress restriction is a VPC firewall
or security group that blocks outbound traffic to any host outside an allowlist.

Railway hobby and starter plans do NOT provide VPC egress firewall rules.  Egress
controls are an enterprise/custom plan feature.  There is no mechanism to configure
outbound firewall rules on the current Railway deployment (RESEARCH Pitfall 6).
The operator should re-verify this against Railway's current pricing page when
considering a plan upgrade.

All outbound HTTP traffic from this service originates from four integration
clients (`SoftSegurosClient`, `MetaCloudClient`, `ChatwootClient`,
`LambdaProyectClient`).  Each client's `base_url` comes from `app/config/settings.py`
environment variables — not from hardcoded constants in feature code.  The one
exception is `META_BASE_URL` in `app/integrations/meta_cloud.py`, which is
hardcoded to `https://graph.facebook.com/{version}` (a constant API endpoint that
cannot change without a code-level version bump).

---

## Decision

Accept the network-level egress gap for v1.  Implement two app-level compensating
controls:

**Control A — Integration client architecture.**
All outbound HTTP is routed through the four named integration clients.  Feature
code never constructs ad-hoc `httpx` clients or makes raw HTTP calls.  Base URLs
come from validated `pydantic-settings` models, not from user input or LLM output.
CLAUDE.md convention: new integrations go in `app/integrations/<name>.py`; no new
outbound HTTP is added to feature or security modules.

**Control B — CI static egress allowlist scan.**
`tests/security/test_egress_allowlist.py` walks `app/**/*.py` with a regex for
`https?://` literals and asserts every captured hostname is in
`ALLOWED_EGRESS_HOSTS`:

```
app.softseguros.com, graph.facebook.com, lookaside.fbsbx.com,
chat.landatech.org, openrouter.ai, api.smith.langchain.com,
smith.langchain.com, sentry.io, localhost, 127.0.0.1
```

This test runs on every CI push (`-m "not integration"`).  Any developer adding a
hardcoded URL outside the allowlist will fail the build with a file:line error
identifying the violation.

The SoftSeguros READ-ONLY invariant is enforced separately by
`tests/test_softseguros_readonly.py` (constrains HTTP verbs, not hosts).

---

## Consequences

**Residual risk:** A compromised third-party dependency (e.g. a malicious package
in the supply chain) could establish outbound connections to arbitrary hosts at the
network layer.  The CI static scan does not protect against runtime dynamic URL
construction, only against hardcoded literals in `app/`.

**Not mitigated by this ADR:**
- Dependency supply-chain exfiltration.
- Environment variable injection (a compromised `SOFTSEGUROS_BASE_URL` could
  redirect traffic; mitigated separately by Railway env var access controls).

**Accepted for v1:** The threat model considers supply-chain compromise out of
scope for v1 (no package signing or lockfile integrity check beyond `uv.lock`).

**Revisit triggers:**
- Railway plan upgrade to a tier that includes VPC/firewall egress rules.
- Addition of a secrets manager or SAST tool that provides runtime egress
  inspection.
- Any Phase 7+ hardening milestone that includes dependency scanning.
