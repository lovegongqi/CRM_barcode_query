# Task 1 Report: Product-code quantity comparison

## Implemented

- Added `_normalize_comparison_product_code(value)` with whitespace trimming, upper-casing, and leading-zero preservation.
- Added `_parse_order_product_quantity(value)` using `Decimal`, accepting non-negative integer representations and rejecting missing, negative, fractional, non-finite, and invalid values.
- Added `_build_service_order_product_comparison(service_products, order_products)` to aggregate service barcode counts and order quantities by normalized product code, preserve first-seen ordering, and report matched, quantity mismatch, order missing, and service missing states.
- Order-code presence is checked independently from quantity, so a valid zero quantity is not treated as a missing code.

## Files changed

- `app.py`
- `tests/test_service_order_product_comparison.py`

## Test evidence

### TDD RED

Command:

```text
PYTHONPATH=. pytest tests/test_service_order_product_comparison.py -q
```

Result: expected failure before implementation: 7 failures/subfailures because the three comparison helpers did not exist; one pre-existing normalization assertion passed.

### TDD GREEN

Command:

```text
PYTHONPATH=. pytest tests/test_service_order_product_comparison.py -q
```

Result: `3 passed, 5 subtests passed`.

### Full suite

Command:

```text
PYTHONPATH=. pytest -q
```

Result: `639 passed, 352 subtests passed in 19.39s`.

## Self-review

- `git diff --check` passed.
- Changes are limited to the requested import, pure comparison helpers, and focused tests.
- No unrelated refactors or generated files were added.

## Concerns

None.
