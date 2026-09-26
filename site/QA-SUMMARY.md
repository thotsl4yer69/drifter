# Field Recorder 01.2 — verification record

Run date: 26 September 2026.

## Passed

- **21 Node publication tests:** dependency-free build, nine routes, original composition, public-output allowlist, twelve SVGs, two PDF signatures, sixteen-entry media archive, real-host metadata, eight-page sitemap, nested-404 base, preview noindex, invalid URL rejection and decoded 1200 × 630 share image.
- **121 Chromium document checks:** nine pages at 320, 360, 390, 768, 900, 1024, 1440 and 1920 pixels; headings/IDs, horizontal overflow and rendered images; mobile navigation and keyboard recovery; recorder range, event pause, cache reuse, end state and reduced motion; connection study, planner, journal filtering; application validation, text export and clipboard fallback; report validation, Markdown/JSON output, unknown values and full-text print preparation; browser SVG-to-PNG conversion; no-JavaScript content and contact links.
- **43 local HTTP/link/archive checks:** every local link and anchor resolves; generated page/asset bytes and MIME types are served correctly; nested missing URLs return branded HTTP 404; HEAD has no response body; all sixteen media archive entries pass CRC validation.
- **Zero browser JavaScript exceptions** in the document test sequence.
- Two PDFs rendered to seven page images and visually reviewed. Website desktop/mobile screenshots and all campaign art were visually reviewed for legibility, composition and clipping.

## Scope of the checks

Chromium URL navigation is restricted in this environment. Browser tests render the actual compiled HTML through `set_content`, embedding local SVG image bytes unchanged. Download links are intercepted to verify the generated Blob content; no application is emailed and no report is uploaded. Python HTTP requests separately test the local Node preview server. No browser policy is bypassed.

These results do not establish public Vercel routing, real-phone rendering, email delivery, vehicle compatibility or hardware acceptance. The existing Vercel project still returns a scope-permission error to the connection.

## Reproduce

The Node suite is included as `publication.test.mjs`. The deliverable package also includes browser/HTTP test scripts and JSON results. Their fixtures are clearly labelled QA, not vehicle evidence. Website forms contain no prefilled test records.

## Corrections found during review

Added a direct contact link across pages, aligned the hardware planner's no-hardware value with the enquiry form, and made print output use the full report text instead of potentially clipped textareas. Reran all release checks after the changes.
