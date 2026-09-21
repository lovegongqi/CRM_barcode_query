# Results and Close Management Design

## Goal

Keep barcode-result browsing separate from service-order closing, while making close-job history visible from every signed-in client.

## Scope

- Keep the existing query-date controls unchanged: users may type dates or use the calendar picker.
- Rename the results workspace entry to **条码列表** and add **结单管理** beside it in the account status bar.
- Make the result-page filter controls compact: four select controls share one desktop row; date controls share the next row; narrow screens wrap naturally.
- Move batch-close initiation and live progress from the barcode result page to a new close-management page.
- Persist finished close jobs and their service-order rows so they survive process restarts and are shared by all clients.
- Let administrators delete one history record or clear all history after the existing browser confirmation prompt. Other users can read the page but cannot mutate history.

## Data model

Store a bounded JSON history file under the existing runtime configuration directory. Each record contains:

- `id`, `started_at`, `finished_at`, and initiating account name;
- selected barcodes, skipped/missing barcodes, and aggregate counts;
- immutable copies of each service-order result: service number, related barcodes, customer names, channel, state, result message, and detail URL;
- final success flag and error message.

Active jobs continue to use the current in-memory scheduler. At completion, one history record is written under a lock. Reads return newest-first records. Delete and clear use the record id and require an administrator account.

## Pages and APIs

- `/` remains the barcode-list page. Its batch-close button and embedded close-progress panel are removed. It retains row selection for existing copy, export, transfer, and deletion actions.
- `/service-close` is the new management page. It loads selectable barcode rows, starts the existing `/api/service-close/start` workflow, polls its status endpoint, and renders persisted history.
- New history APIs provide list, delete-one, and clear-all operations. They use the existing `results` permission for read/start and administrator authorization for delete/clear.

## UX

The two workspace links sit at the left edge of the existing account-status bar. The active entry uses the established highlighted button treatment. On the management page, a current-job section appears above history; history rows expose service-order details with the same modal already used by the barcode list. Empty history states are explicit. Delete and clear have separate confirmation wording.

## Verification

- Backend tests cover persistence, newest-first retrieval, administrator-only deletion, and clearing history.
- Frontend contract tests cover the new links, compact filter layout, unchanged native date inputs, and absence of batch-close controls from the barcode list.
- A browser check starts an already-closed sample service order and verifies the saved history is visible after a reload from another tab.
