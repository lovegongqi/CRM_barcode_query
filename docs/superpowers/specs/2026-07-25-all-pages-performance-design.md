# All Pages Performance Design

## Goal

Reduce UI stalls, repeated network transfer, unnecessary disk parsing, and hidden-page work across the CRM query, results, transfer, product-library, settings, login, and permission pages without removing data or changing business behavior.

## Evidence

- A synthetic 2,000-item query job currently produces a status response of about 993 KB every second. Only 30 running/failed rows require display in the measured case, totaling about 22 KB.
- The results page calls `/api/barcodes` and `/api/filter-options` separately. Both routes call `scan_barcodes()`, which reparses every result HTML file.
- The results page rebuilds every result node at once and repeats the full refresh every 30 seconds even when the tab is hidden.
- The transfer page requests and rerenders every persisted record once per second even when no record changed.
- The product-library page receives and rewrites the complete online-query log on every poll.
- `static/log_modal.js` stores up to 8,000 rows in `sessionStorage` and maintains the same hidden DOM even when the log dialog is closed.

## Architecture

### Query Page

The backend retains every query item for scheduling and totals, but `_background_query_status_payload` returns only `running`, `error`, and `stopped` items. `pending_count`, completed counts, failures, and retry data continue to come from the complete server job. The frontend applies the same filter defensively and removes successful rows on the next refresh.

### Results Page

Add a process-local barcode snapshot keyed by result/archive filenames, sizes, mtimes, and barcode metadata mtime. Unchanged requests reuse parsed fields. `/api/barcodes` returns the filter definitions and a revision in the same response; a matching client revision returns `unchanged: true`.

The frontend allows only one load request at a time, skips periodic refresh while hidden, and renders result rows in cancellable chunks. All filtered results remain available; no pagination or record limit is introduced.

### Transfer Page

Maintain a process-specific monotonic transfer-record revision. The records API accepts the client's revision and returns `unchanged: true` without serializing records when nothing changed. Poll every second while a record is active, every five seconds while idle, and pause requests while hidden.

### Product-Library Page

Assign sequence IDs to online-query log rows. The status route accepts `since` and returns only newer logs. The frontend merges the new rows, keeps the existing 300-row server limit, and pauses polling while hidden.

### Shared Logs And Static Pages

Reduce global page-log history to 1,000 rows, render at most 500 rows when the dialog is open, do not maintain hidden log DOM, and debounce `sessionStorage` writes. Settings polling pauses while hidden. Login and permission pages have no recurring work and need no performance changes.

## Error Handling

- Revision mismatches or process restarts return a full snapshot.
- A failed refresh leaves current rendered data intact and retries on the next scheduled cycle.
- Visibility return triggers an immediate refresh.
- Chunked results rendering uses a generation token so stale render work cannot overwrite a newer filter or refresh.

## Testing

- Backend tests cover filtered query items, preserved totals, barcode snapshot reuse/invalidation, combined filters/revision responses, transfer record revisions, and incremental product-library logs.
- Frontend contract tests cover visible query states, chunked result rendering, hidden-page guards, adaptive transfer polling, incremental matching logs, and bounded global log history.
- Full unit tests, Python compilation, JavaScript/browser loading, and diff checks run before completion.
