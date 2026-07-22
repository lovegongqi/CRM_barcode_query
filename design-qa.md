# Design QA

## Visual source

- Source screenshots: `design-qa-mobile-before.png` and the annotated 430 × 932 mobile query reference
- Source viewport: 430 × 932
- Requested corrections: place the shared EcoWater logo directly to the left of each work-page title, remove the repeated tool-account text from query and transfer, preserve the fixed bottom navigation, and prevent page-level horizontal overflow.

## Implementation capture

- Implementation screenshot: `design-qa-mobile-header-after.png`
- Implementation viewport: 430 × 932

## Comparison history

### Pass 1

- The mobile navigation was fixed at the top, contrary to the requested bottom placement.
- The transfer form and realtime-record cards expanded beyond the viewport because a wide table influenced the single-column grid's minimum width.
- Form controls and transfer-channel buttons were clipped on the right.

### Final pass

- At 320px, 390px, and 430px, all five work pages (`/crm`, `/`, `/transfer`, `/product-library`, `/accounts`) report document scroll width equal to viewport width.
- Across all 15 mobile route/width combinations, the logo is fixed at `left: 12px`, spans to 50px, and the title content begins at 66px; the two elements no longer overlap.
- The title and logo share the same 18px top baseline on every mobile work page.
- Navigation is fixed 10px above the bottom safe area and remains at the same position while the document scrolls.
- Page content reserves bottom space so the navigation does not cover the final records.
- Transfer cards fit within the 430px viewport; wide tables scroll only inside their own table wrapper.
- Query keeps browser state, CRM state, and the logout control without showing `工具账号：admin`.
- Transfer keeps CRM state and the logout control without showing `工具账号：admin`.
- At 1440 × 900, all five pages retain the previous 24px desktop logo inset, 104px title content start, and navigation-below-title layout.
- Browser console check returned no errors or warnings.
- Side-by-side comparison confirms the intended changes only: logo/title lockup, compact status row, and visible fixed bottom navigation. Card spacing, typography, colors, controls, and desktop composition remain unchanged.
- The requested dark glass visual treatment, typography, spacing, controls, and responsive structure remain unchanged outside the corrected mobile layout.

## Final result

passed
