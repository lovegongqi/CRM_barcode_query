# Android Runtime and Portal Feasibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that an APK can run this project's pages and local data without NAS, and can reach CRM and GYJ from Android before migrating all production workflows.

**Architecture:** Bundle the existing Flask application and web assets in an Android WebView host, run Flask only on device loopback, and store its data in app-private storage. Validate a separate WebView/session bridge for each external portal. This plan is the first, independently testable phase of the [full Android design](../specs/2026-10-16-standalone-android-design.md); follow-on plans for CRM operations, GYJ operations, and release are written only after portal feasibility is measured.

**Tech Stack:** Android Kotlin, Gradle, WebView, Chaquopy Python, Flask, SQLite, pytest, Android instrumented tests.

**Spec:** `docs/superpowers/specs/2026-10-16-standalone-android-design.md`

## Global Constraints

- Runtime network targets: CRM and GYJ only; no NAS dependency. Do not bundle credentials, cookies, or NAS data.
- First install has empty local data. The device-private data directory survives process restart.
- All eight pages must ultimately work before a production APK is published; this first plan only validates the runtime and portal access.
- Android build must not try to bundle Playwright's desktop Chromium or claim portal operations work merely because the pages render.
- Keep the desktop/NAS path unchanged. Isolate Android-only startup and portal code behind platform-specific entrypoints.

## Review Focus

- A second app opening the loopback port must not gain access to local records: test same-origin/session protection in Task 2.
- A process restart must reopen the same private records without deleting or migrating them: test in Task 3.
- CRM HTTP fallback must be restricted to the CRM host, not global cleartext traffic: inspect merged manifest in Task 4.
- Expired portal cookies must show login required, not a successful empty query: test in Tasks 4 and 5.
- A portal response with HTTP 200 but a business error must fail the probe: test in Tasks 4 and 5.

---

### Task 1: Buildable Android host with local pages

**Files:**
- Create: `android/settings.gradle.kts`, `android/build.gradle.kts`, `android/gradle.properties`, `android/gradlew`, `android/gradle/wrapper/*`, `android/app/build.gradle.kts`
- Create: `android/app/src/main/AndroidManifest.xml`, `android/app/src/main/java/cn/lovegongqi/crmbarcode/MainActivity.kt`
- Create: `android/app/src/androidTest/java/cn/lovegongqi/crmbarcode/HostStartupTest.kt`

**Interfaces:** Produces `MainActivity.openLocalPage(path: String)` for later tasks; package id `cn.lovegongqi.crmbarcode`, minimum SDK 26. The host displays an explicit startup error until Task 2 starts the local server.

- [ ] **Step 1: Prepare Android build tools** — use official Android SDK command-line tools and a JDK compatible with the selected Android Gradle plugin; record installed SDK versions in the build file.
- [ ] **Step 2: Write failing instrumented startup test** — assert the host opens without crashing and displays “本机服务未启动” while no local server exists; assert it never navigates to NAS.
- [ ] **Step 3: Run `cd android && ./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=cn.lovegongqi.crmbarcode.HostStartupTest`** — expect failure before the host exists.
- [ ] **Step 4: Add the smallest buildable host and Gradle wrapper** — WebView enabled only for bundled local UI; external portal views are separate and not exposed as local navigation.
- [ ] **Step 5: Run `cd android && ./gradlew assembleDebug connectedDebugAndroidTest`** — expect build and startup test pass; record APK path and SHA-256.
- [ ] **Step 6: Commit** — `feat(android): add local page host`.

### Task 2: Start Flask locally without desktop Chromium

**Files:**
- Create: `android/app/src/main/python/android_bootstrap.py`, `android/app/src/main/java/cn/lovegongqi/crmbarcode/LocalServer.kt`
- Modify: `android/app/build.gradle.kts`, `android/app/src/main/java/cn/lovegongqi/crmbarcode/MainActivity.kt`, `app.py`
- Test: `tests/test_android_bootstrap.py`, `android/app/src/androidTest/java/cn/lovegongqi/crmbarcode/LocalServerTest.kt`, `android/app/src/androidTest/java/cn/lovegongqi/crmbarcode/PageNavigationTest.kt`

**Interfaces:** `android_bootstrap.start(resource_dir: str, data_dir: str, session_dir: str, host: str, port: int) -> None` sets resource/data paths before importing `app`; `LocalServer.start(): URL` returns the loopback origin after `/login` responds. Startup must not launch Playwright workers. Generate a random per-install Flask secret and session protection for local requests.

- [ ] **Step 1: Write failing Python and instrumented tests** — imported Android runtime starts no desktop browser threads; server binds loopback only; all eight local page URLs render HTML or redirect to the local login when unauthenticated; request without local session protection cannot read `/api/barcodes`.
- [ ] **Step 2: Run `PYTHONPATH=. pytest -q tests/test_android_bootstrap.py` and the Android test** — expect failures before bootstrap exists.
- [ ] **Step 3: Bundle the runtime `.py` modules, Flask and pure-Python dependencies with Chaquopy, copy `templates/` and `static/` into app-private versioned resources, then implement the bootstrap and guarded `app.py` startup**.
- [ ] **Step 4: Run the same tests plus `cd android && ./gradlew assembleDebug`** — expect green and no Playwright browser artifact in APK.
- [ ] **Step 5: Commit** — `feat(android): start private local Flask runtime`.

### Task 3: Empty local data and restart persistence

**Files:**
- Create: `android/app/src/main/java/cn/lovegongqi/crmbarcode/AppStorage.kt`
- Modify: `android/app/src/main/java/cn/lovegongqi/crmbarcode/LocalServer.kt`, `android/app/src/main/python/android_bootstrap.py`
- Test: `android/app/src/androidTest/java/cn/lovegongqi/crmbarcode/StorageRestartTest.kt`

**Interfaces:** `AppStorage.dataDir` and `AppStorage.sessionDir` are app-private directories passed to bootstrap; neither resolves to NAS or shared external storage.

- [ ] **Step 1: Write failing restart test** — fresh install has no barcode/history records; create a local-only record, kill/restart process, confirm it remains; assert no network request to `mlmll.cn`.
- [ ] **Step 2: Run `cd android && ./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=cn.lovegongqi.crmbarcode.StorageRestartTest`** — expect failure before storage wiring.
- [ ] **Step 3: Wire private directories and disable desktop data migration on Android** — keep the desktop path unchanged.
- [ ] **Step 4: Run instrumented test and `PYTHONPATH=. pytest -q tests/test_android_bootstrap.py`** — expect pass.
- [ ] **Step 5: Commit** — `feat(android): persist phone-local records`.

### Task 4: CRM read-only portal probe

**Files:**
- Create: `android/app/src/main/java/cn/lovegongqi/crmbarcode/CrmPortalProbe.kt`, `android/app/src/main/java/cn/lovegongqi/crmbarcode/PortalWebView.kt`
- Create: `android/app/src/test/java/cn/lovegongqi/crmbarcode/CrmPortalProbeTest.kt`
- Modify: `android/app/src/main/AndroidManifest.xml`

**Interfaces:** `CrmPortalProbe.run(barcode: String): ProbeResult` returns `Success(model, code, barcode)`, `LoginRequired`, or `Failure(message)`; it never persists or mutates CRM records. `PortalWebView` shares only its own CRM session with the probe. Full CRM operations remain disabled until a follow-on plan.

- [ ] **Step 1: Write failing tests** — fixture responses cover successful barcode lookup, expired cookie, HTTP 200 business error, and wrong barcode in result.
- [ ] **Step 2: Run `cd android && ./gradlew testDebugUnitTest --tests '*CrmPortalProbeTest*'`** — expect failure.
- [ ] **Step 3: Implement login/cookie and read-only query against the observed CRM contract; use a narrow host allowlist for HTTP only if HTTPS verification fails**.
- [ ] **Step 4: Run unit tests; inspect merged manifest for host-scoped cleartext; manually confirm one permitted test barcode on Android with NAS unreachable** — record redacted result and timing in `docs/android/portal-feasibility.md`.
- [ ] **Step 5: Commit** — `feat(android): prove CRM read-only access`.

### Task 5: GYJ read-only portal probe and decision

**Files:**
- Create: `android/app/src/main/java/cn/lovegongqi/crmbarcode/GyjPortalProbe.kt`
- Create: `android/app/src/test/java/cn/lovegongqi/crmbarcode/GyjPortalProbeTest.kt`
- Modify: `docs/android/portal-feasibility.md`

**Interfaces:** `GyjPortalProbe.run(productCode: String): ProbeResult` returns stock/product data, `LoginRequired`, or `Failure(message)` and does not submit an ERP form. It must keep GYJ cookies separate from CRM cookies.

- [ ] **Step 1: Write failing tests** — fixture responses cover valid stock, expired cookie, HTTP 200 business error, and malformed stock data.
- [ ] **Step 2: Run `cd android && ./gradlew testDebugUnitTest --tests '*GyjPortalProbeTest*'`** — expect failure.
- [ ] **Step 3: Implement GYJ login/session and one read-only stock/product request, including WebView interaction if the legacy report requires it**.
- [ ] **Step 4: Run unit tests and manually confirm one test product on Android with NAS unreachable** — record redacted evidence. If either portal or Chaquopy cannot pass, stop and propose a revised runtime rather than releasing an APK.
- [ ] **Step 5: Commit** — `feat(android): prove GYJ read-only access`.

After Task 5, use the measured portal contracts to write separate plans for CRM write operations, GYJ write operations, remaining local pages, and release QA. The production APK is published only after those plans meet the full design's acceptance criteria.
