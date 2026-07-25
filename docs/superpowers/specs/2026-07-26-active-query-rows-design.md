# Active Query Rows Design

## Goal

Keep large background-query batches responsive by limiting the realtime table and status payload to actionable barcode rows.

## Behavior

- Show rows whose state is `running`, `error`, or `stopped`.
- Do not show rows whose state is `waiting` or `success`.
- Remove a successful row on the first status refresh after it reaches `success`.
- Keep failed and stopped rows available for inspection and retry.
- Keep the summary badge accurate by using the job-level `total`, `completed`, `success_count`, `failed_count`, and `failed_barcodes` fields.
- When no visible rows remain, show `当前无查询中或失败条码`.

## Architecture

The backend continues to retain every item internally so queue scheduling, stop accounting, totals, and final results remain correct. `_background_query_status_payload` filters the copied `items` array to `running`, `error`, and `stopped` before serializing the response. It calculates `pending_count` from the complete internal item list rather than the filtered response.

The frontend applies the same state filter before rendering. This defensive filter protects the UI when it receives an older or cached unfiltered payload, while the server-side filter reduces network transfer and JSON parsing for large batches.

## Testing

- A backend test creates waiting, running, success, error, and stopped items and proves only running/error/stopped are returned while totals and pending count remain accurate.
- A frontend contract test proves the realtime table uses the same visible-state filter and the new empty-state text.
- Existing background query and full regression tests must continue to pass.
