# Website / touchscreen main integration — 28 September 2026

## Source integration

User-authorized normal merges, without a force push:

- PR #84: Field Recorder website and media kit, merge `efc0418432571788fe2b4860957db851a9d09992`.
- PR #85: in-car acceptance hardening, merge `572b5fbec20b21079cd0e1804db323839395aef9`.
- PR #86: cockpit touchscreen and optional perception work, merge `845a0acbf14955f17139b446873f7d74c17e7659`.

This continuation is based on the last merge. It does not merge older, overlapping PR #65 or #70.

## Website changes

The existing Field Recorder paper/ink identity, original homepage ribbon, nine routes, media kit, report writer and email-preparation flows are preserved. The homepage now has a See the touchscreen anchor and a dedicated LEDGER DRIVE section. It describes the seven workspaces, DAY/NIGHT/AMBER themes, DATA window, connection strip, independent OBD speed and per-PID stale-data behaviour.

Canonical new content: `site/touchscreen-page.mjs`. Canonical new styles: `site/touchscreen-site.css`. `site/complete-site.mjs` integrates them at explicit homepage insertion points and fails if those points drift. The original `home-source.html` is unchanged. The source fingerprint includes both new source files and the published image. `version.json` includes the preview provenance.

`site/assets/previews/touchscreen-drive.svg` is a static 800 x 480 vector export made from actual cockpit components at source commit `5b83a6a4243e38ff1888e4542a1b323388d07937`. In this continuation, those components and their display-state module were transpiled, rendered in local Chromium with React 16 and system-font/theme fixtures, and exported using their computed DOM geometry, colours and text. It is not a production React 18 app capture or a physical Pi/vehicle test. Its SHA-256 is `7587830c1b4cee4101b054f8e93f7001cb5d4e34cf70b09b963c2383202de3d5`. The website explicitly retains this distinction and the unverified Hailo/incomplete inference status. The static SVG contains no scripts, external references or bundled fonts; the builder checks its exact hash before publishing it.

## Integration repairs

The merged acceptance source has Git blob `797f2822152afda43f72aa5293337f28e625b111`. The narrow field installer previously pinned the pre-merge #85 blob and would reject the new source. Its pin is corrected and both the original baseline and the reviewed #85 revision are accepted as upgrade sources. Unknown revisions remain rejected, backups remain mandatory, and repeat installation remains idempotent. The installer still does not restart services or update the OS. The new integrity test compares the pin against actual repository source bytes in a complete checkout.

The existing GitHub Pages workflow uploaded `site` directly. It now runs publication tests, builds the site using Pages metadata and publishes only `site/dist`; source files and stale generated HTML are no longer the intended deployment artifact. This corrects the existing publishing path rather than adding another hosting provider. The workflow records the actual Git commit in the generated version metadata.

## Verification performed for this continuation

- 12/12 new Node website source/publication contracts passed locally.
- 54/54 cockpit display-state and adapter tests passed again using the supplied source package.
- 40/40 Chromium assertions passed for the new website section at 320x700, 390x844, 800x480, 1024x600 and 1440x1000. Checks cover overflow, image loading/aspect ratio, responsive columns, captions, labels, link target sizes and focus visibility. No JavaScript exceptions. Desktop and phone captures were visually reviewed.
- New JavaScript syntax, installer Bash syntax and Python test compilation passed. The reviewed-upgrade-path integrity test passed locally; the installer source pin was compared with the live GitHub blob metadata.

The browser checks render the isolated new section with the actual new markup/CSS/vector export and existing site brand tokens/reset/shell rules. They do not exercise all site routes, the recorder interaction, a public host or a physical touchscreen. The complete site build and existing 21 publication tests require the rest of the repository; they are included in the updated CI workflow but were not rerun locally in this continuation. The actual-byte installer pin test also requires the complete checkout and was not run locally. Earlier PR validation records are not counted as newly executed tests.

## Outstanding external checks

At the start of this continuation, GitHub run `36324256515` failed before executing any step (lint/test job `108633847934`, `steps=[]`, no allocated runner). That is not a passing CI result or an identified code failure. Check the new main workflow runs for the final publication result.

The connected Vercel app returned HTTP 403 for team `js-projects-cdc6aae4` / `team_LEZY0kMrTlQYKiF688NBggZ5`. The existing hosting connection needs authorization to that team before its deployment can be inspected or managed. No Vercel production publication is claimed by this source update.

No changes were installed on the user's Pi. Physical issue #67 remains open: display/touch calibration, recovery, intended-power cold starts and the 30-minute telemetry run still require recorded hardware evidence. Hailo inference remains incomplete and unverified.
