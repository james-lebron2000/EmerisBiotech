# Evidence location UI preview

These are explicitly synthetic offline DTO1.4 fixtures rendered by the shipped public page, not real protocol inclusion, an actual clinical forecast, a deployment or clinical validation. No external source or model was called. Existing test ZIP delivery uses only a temporary loopback fixture listener.

The expanded reference shows Recorded pages, a separately stored legacy Recorded page, Recorded section, Document version and Recorded date. Pages are displayed as recorded metadata; no common PDF pagination or #page anchor is inferred. Missing location/version retain explicit empty states.

- `synthetic-evidence-locations-1440.png`:1440×900 desktop viewport, full page.
- `synthetic-evidence-locations-1280.png`:1280×900 desktop viewport, full page.
- `synthetic-evidence-locations-375.png`:375×812 mobile viewport, full page.
- `provenance.json`:shipped asset hashes, screenshot hashes, exact fixture/browser scope.

Focused `tests/ui_arena_public.cjs`:36 contract checks passed, zero page errors, Node22.22.3 / Playwright module1.62.1 / preinstalled Chromium1194 (141.0.7390.37). The new checks cover all3fields, old/missing/null/empty metadata, model-specific attribution, no expansion request, HTML escaping, credential/control/Cf/bidi/lone-surrogate suppression, value bounds, maximum100pages and Unicode length limits, and1440/1280/375 layouts. Both owned JavaScript syntax checks and git diff --check passed. No canonical/full suite or real-record/server acceptance was run by this UI owner.

Default collapsed desktop layout remains unchanged; the existing synthetic approved-calendar modules still end at y986.40625 under the1000px bound. Expanded content is intentionally readable and may require scrolling. All3screenshots were visually reviewed.
