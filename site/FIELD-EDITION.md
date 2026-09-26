# DRIFTER VIM — Field Recorder Edition

## Design authority

The redesigned `index.html` is self-contained: its CSS, JavaScript, original vector wordmark and procedural canvas illustration are inline. No runtime packages, remote fonts, stock imagery or downloaded hero video are required. Existing legacy assets remain in the repository but are not dependencies of this homepage.

Direction: automotive editorial / a physical record of time. The identity is a purpose-drawn geometric DRIFTER wordmark, oversized composition, off-white paper against warm graphite, fine technical rules, restrained monospace labels and an amber recording cursor. This replaces the previous neon card/dashboard layout.

The hero line is **A memory for your machine.** The primary interaction is the capture-window sculpture: a layered procedural time ribbon that can be scrubbed from -90 to +45 seconds. Its controls are native, keyboard-accessible and usable on touchscreens. The image is expressly an illustration, not captured vehicle telemetry.

## Implemented interactions

- Responsive navigation; mobile menu closes on Escape, link selection and return to desktop width.
- Timeline range, three chapter selections and play/pause ending at the final frame.
- Pointer parallax disabled under reduced-motion preferences.
- Animation pauses when the illustration leaves the viewport or the tab is hidden.
- Native expandable system and FAQ sections.
- Native application dialog with required-field validation and focus restoration.
- Application composer prepares a business-email message. It does not claim to send applications or write to a database.
- Copy-to-clipboard with selectable-text fallback.
- Visible primary content and a descriptive illustration fallback without JavaScript.
- Matching media notes and not-found page.

## Browser verification performed

Chromium / Playwright, rendering local HTML documents. This is local browser verification, not production network or vehicle validation.

Passed:
- No horizontal page overflow at 320, 360, 390, 768, 1024, 1440 and 1920 pixels.
- Unique IDs and valid internal anchors.
- Range Home/End states (-90s / +45s) and corresponding accessible labels.
- Chapter buttons update the relative-time state.
- Play reaches +45s and returns to paused.
- System and FAQ disclosure opening.
- Mobile menu opening, Escape closing, link navigation and scroll unlocking.
- Required form fields and copy-text fallback; no application was transmitted.
- Dialog Escape and focus restoration.
- No-JavaScript content visibility.
- Reduced-motion playback and parallax handling.
- Zero browser JavaScript exceptions during the test sequence.

## Product evidence

Physical acceptance remains separate from website testing. The website links to issue #67 and does not turn pending vehicle acceptance into passed results. No invented customers, testimonials, prices, follower counts, hardware housings or live sensor readings were added.

## Publication status

This branch contains the redesigned website. Do not describe this version as live on the existing Vercel production alias until that deployment has been replaced and verified. The connected Vercel deployment reader denied access to the existing project; the design-import endpoint only accepts Claude-hosted bundles and did not import this GitHub HTML.
