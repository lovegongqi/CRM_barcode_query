# CRM SMS Webhook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept a forwarded CRM SMS and complete the active CRM bulk login without manual code entry.

**Architecture:** A single token-protected Flask endpoint validates the specific SMS format and feeds the existing bulk-login submission path. The token lives in the persistent NAS data volume. HTTPS exposure is a separate deployment check.

**Tech Stack:** Flask, Python unittest/pytest, Docker Compose, Synology NAS.

**Spec:** `docs/superpowers/specs/2026-10-28-crm-sms-webhook-design.md`

## Global Constraints

- Accept only the user-provided “【怡口净水】您的验证码” four-digit template.
- Do not log or persist the SMS body, code, or token.
- Preserve existing manual login and NAS named volumes.

## Review Focus

- SMS without the exact CRM signature and code phrase: ignore without changing a login job.
- Missing or wrong token: reject before parsing the body.
- No active job, duplicate delivery, or old job: acknowledge without a browser action so the phone does not retry stale codes.
- SMS arriving while slots are opening: retain the code for the existing late-slot path.
- Multiple active login jobs: acknowledge without choosing one silently.

### Task 1: Receiver and tests

**Files:** Modify `app.py`; test `tests/test_bulk_login.py`.

- [x] Write route tests for authentication, format, job state, duplicate, and accepted SMS.
- [x] Run focused tests and observe failure from the missing route.
- [x] Implement the minimal token check, parser, active-job selection, and existing submission dispatch.
- [x] Run focused and full test suites; inspect the diff.
- [x] Commit the verified change (`b3582f7`).

### Task 2: NAS and phone configuration

**Files:** NAS persistent token file and deployment; no credential in Git.

- [x] Preserve named volumes and deploy the exact tested commit with a rollback image.
- [x] Verify token rejection and acceptance with synthetic SMS without triggering real login.
- [x] Verify a valid external HTTPS route: `https://mlmll.i234.me:5012/login` returned 200 with a valid certificate.
- [ ] Test one real forwarded SMS with an active CRM login job.
