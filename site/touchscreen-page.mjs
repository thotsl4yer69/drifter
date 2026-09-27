import {createHash} from 'node:crypto';

/** Public touchscreen description. The preview is not a live vehicle connection. */
export const previewProvenance = Object.freeze({
  sourceCommit: '5b83a6a4243e38ff1888e4542a1b323388d07937',
  capture: 'Static vector export of Chromium-rendered React 16 components; synthetic test data',
  image: 'assets/previews/touchscreen-drive.svg',
  width: 800,
  height: 480,
  physicalAcceptance: 'Not established by this preview',
});

export function validatePreview(bytes) {
  const source = bytes.toString('utf8');
  if (!source.startsWith('<svg ') || !source.includes('viewBox="0 0 800 480"')
      || !source.includes('width="800" height="480"')
      || /<script|<foreignObject|href=|url\(/i.test(source)
      || createHash('sha256').update(bytes).digest('hex') !== '7587830c1b4cee4101b054f8e93f7001cb5d4e34cf70b09b963c2383202de3d5') {
    throw new Error('The touchscreen preview must be the reviewed static 800 × 480 SVG.');
  }
}

export function touchscreenSection() {
  return `<section class="touchscreen-section shell" id="touchscreen" aria-labelledby="touchscreen-title">
  <div class="touchscreen-rail"><span>Interface / LEDGER DRIVE</span><span>Touchscreen update / September 2026</span></div>
  <div class="touchscreen-heading">
    <h2 id="touchscreen-title">The useful things.<br><em>Within reach.</em></h2>
    <div><p class="touchscreen-lead">Speed. Engine state. Connection state.<br>A dedicated view for the vehicle node.</p><p>The updated cockpit puts the four core readings up front and keeps the wider DRIFTER workspaces one touch away. The recorder stays local; the website is not connected to your car.</p></div>
  </div>
  <figure class="touchscreen-preview">
    <div class="touchscreen-preview-label"><span>800 × 480 / Component preview</span><strong>SYNTHETIC TEST DATA</strong></div>
    <img src="assets/previews/touchscreen-drive.svg" width="800" height="480" loading="lazy" decoding="async" alt="DRIFTER LEDGER DRIVE component with speed, RPM, coolant and voltage, connection indicators, and seven workspace buttons. Every reading shown is synthetic test data." aria-describedby="touchscreen-caption">
    <figcaption id="touchscreen-caption">Browser-rendered components from the actual cockpit source, using a React 16 test harness. Not a physical Pi or vehicle capture, and not the complete production React 18 application. Static vector export with system fonts; the production font bundle is not represented.</figcaption>
  </figure>
  <div class="touchscreen-features">
    <article><span class="touchscreen-index">01 / Read</span><h3>Four readings. No guesswork.</h3><p>OBD speed, RPM, coolant and voltage have a dedicated DRIVE layout. OBD speed does not depend on getting a GPS fix. Missing or disconnected readings become dashes; a genuine zero remains a zero.</p></article>
    <article><span class="touchscreen-index">02 / Reach</span><h3>Built around touch.</h3><p>Compact landscape screens use bottom navigation. DAY, NIGHT and AMBER themes change the presentation. Primary navigation, theme and DATA controls measured at least 58 CSS pixels in the component checks; physical readability still needs testing.</p></article>
    <article><span class="touchscreen-index">03 / Inspect</span><h3>Detail when you need it.</h3><p>The DATA window scrolls through extra detail, including trip and Vivi information on compact screens. Close or Escape dismisses it, and keyboard focus returns to its opening control.</p></article>
    <article><span class="touchscreen-index">04 / Know</span><h3>Old data is not live data.</h3><p>Speed and RPM expire after 3 seconds without a fresh PID receipt. Coolant and voltage expire after 10 seconds. A reconnected link needs new readings; cached snapshots cannot refresh these values.</p></article>
  </div>
  <div class="touchscreen-workspaces"><h3>One cockpit. Seven workspaces.</h3><p class="touchscreen-workspace-names">DRIVE · MAP · DIAG · RF · FOOT · VIVI · SYSTEM</p><p>Vehicle diagnostics, mapping, research tools and system information remain in the application. RF and FOOT retain their existing operating-mode restrictions. This website preview does not operate those tools.</p></div>
  <div class="touchscreen-status"><div><span class="touchscreen-index">Connection strip</span><h3>OBD / GPS / HAILO / CAM / SDR / REC / LINK</h3><p>Service availability is not the same as verified sensor output. A dashcam service that is online can be READY without being RECORDING.</p></div><div><span class="touchscreen-index">Optional perception / UNVERIFIED</span><h3>Hailo remains experimental.</h3><p>The Hailo inference and ONNX decoding paths still need implementation and validation. The dashboard does not turn an empty detection list into “road clear”. Perception is experimental context, not collision protection.</p></div></div>
  <div class="touchscreen-next"><p><strong>The next proof is on hardware.</strong> Touch calibration, display and adapter recovery, ten cold boots and a 30-minute telemetry run remain physical acceptance work.</p><div><a href="field-guide.html">Open the field guide ↗</a><a href="https://github.com/thotsl4yer69/drifter/blob/5b83a6a4243e38ff1888e4542a1b323388d07937/docs/TOUCHSCREEN_REVIEW_2026-09-27.md" target="_blank" rel="noopener noreferrer">Read the engineering evidence ↗</a></div></div>
</section>`;
}

export function enhanceHome(source) {
  const boundary = '<section class="system section shell"';
  const action = '<div class="hero-actions">';
  if ((source.match(/<section class="system section shell"/g) || []).length !== 1
      || (source.match(/<div class="hero-actions">/g) || []).length !== 1
      || source.includes('id="touchscreen"')) {
    throw new Error('Homepage composition changed; review touchscreen insertion points.');
  }
  return source.replace(boundary, touchscreenSection() + '\n ' + boundary)
    .replace(action, action + '<a class="text-cta touchscreen-jump" href="#touchscreen">See the touchscreen <span class="arrow" aria-hidden="true">↗</span></a>');
}
