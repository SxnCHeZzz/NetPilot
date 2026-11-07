# NetPilot compact UI — visual QA

## Compared material

- Source: `codex-clipboard-efcc9c6c-1e5b-4467-ae2a-5e3261c6b093.png`
- Native Windows captures: `.superpowers/sdd/2026-11-06-premium-icon-polish/visual/compact-overview.png`, `compact-details.png`, `compact-attention.png`
- App content viewport: 308 × 392 logical pixels. The native QA captures include the Windows title bar; the production window remains frameless.

## Review

| Priority | Finding | Result |
|---|---|---|
| P1 | Header controls were visually 42 px and read as oversized glowing buttons. | Fixed: 30 px visual square inside a 34 px hit area, 16 px vector, 9 px radius, no idle glow. |
| P1 | Compacting the window could clip the detail card and footer. | Passed in native overview and details captures at 308 × 392; labels, actions, footer and status tabs remain visible. |
| P2 | Error state contradicted the Zapret summary by showing a green working icon. | Fixed: transient error state is red and restores the underlying semantic state after the notice closes. |
| P2 | Back/settings/minimize lacked restrained, role-specific feedback. | Fixed: 135 ms hover, 2 px back shift, 20° gear rotation and press-only minimize feedback. |
| P2 | DPI behavior was not explicitly covered for the compact header. | Covered by render tests at 100%, 125% and 150% scaling. |
| P3 | The supplied reference is a header strip rather than a complete app screen. | Full-screen spacing follows the already approved NetPilot layout while matching the reference header density. |

## Final result

**passed** — no remaining P0, P1 or P2 visual differences in the requested scope.
