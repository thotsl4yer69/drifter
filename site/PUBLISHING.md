# DRIFTER VIM — publish Field Recorder 01.1

## Use the existing Vercel project

Project: `drifter-vim`, under `js-projects-cdc6aae4`.
Repository: `thotsl4yer69/drifter`.
Website branch: `design/field-recorder-edition` (PR #84).

The current ChatGPT Vercel connection receives a 403 for that team's project. Reauthorize the connection using the account/team that owns it. Publishing directly from that team's Vercel dashboard does not require the ChatGPT connection to be working.

## Publish this version now, without merging vehicle code

1. Open the existing `drifter-vim` project in Vercel. In Settings → Git, connect `thotsl4yer69/drifter` if it is not already linked.
2. Under Settings → Build and Deployment, use the settings below.
3. Under Settings → Environments → Production → Branch Tracking, select `design/field-recorder-edition` and save. This publishes the website branch; it does not change the vehicle's `main` checkout.
4. Open Deployments → Create Deployment and select that branch. Wait for READY, then open the production URL shown by Vercel.

| Setting | Value |
|---|---|
| Framework preset | Other |
| Root directory | `site` |
| Build command | `node build.mjs` |
| Output directory | `dist` |
| Install command | Empty; no package installation is required |

The build/output values are also committed in `site/vercel.json`. The root directory is a project setting, not a field in that JSON.

Do not redeploy the old `main` commit and expect this design to appear. After PR #84 is merged, the production branch can be changed to `main`.

## What gets published

The publisher creates `site/dist/` from the Field Recorder source. Only that folder is served. It includes the homepage, media notes, data/contact page, 404, wordmark, generated PNG share image, favicon, manifest, crawler rules and version record. A sitemap is generated when a public site URL is known. Source files, tests and old dashboard assets are not included.

The homepage remains self-contained for local review: styles and client JavaScript are inlined in the generated HTML. The renderer, email composer and local interactions require no API key or database.

## URL and custom domain

Vercel's `VERCEL_PROJECT_PRODUCTION_URL` supplies the production hostname at build time. `SITE_URL` overrides it after a custom domain is configured. Keep `SITE_URL` unset until there is a real final URL; the build never invents one. Preview environments receive noindex metadata and restrictive crawler rules.

A custom domain is not necessary to publish. Add it to the same Vercel project later, follow the DNS values Vercel gives for that domain, set `SITE_URL` to the actual HTTPS domain, then redeploy.

## Confirm the published version

- Homepage shows “A memory for your machine.” and the geometric DRIFTER wordmark.
- “Jump to event” sets the recorder to 00:00.
- `/version.json` reports `Field Recorder 01.1` and, for Git deployments, the built commit.
- `press.html` and `privacy.html` open correctly.
- Application preparation does not claim to send an email. It opens the user's mail app or copies the application.

## Repeatable local checks

From the repository's `site` directory:

```sh
node --test publication.test.mjs
node build.mjs
```

The nine publishing tests cover URL handling, public output, previews and PNG decoding. The accompanying browser report records 51 local Chromium assertions. Browser tests are document-rendering tests; they are not proof of a successful Vercel build, public HTTP routing, real-phone behavior or vehicle acceptance.

## Official publishing references

- https://vercel.com/docs/builds/configure-a-build
- https://vercel.com/docs/git
