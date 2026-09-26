# Publish DRIFTER / Field Recorder 01.2

## Current delivery state

The complete website is built and locally tested. The existing Vercel project is `drifter-vim` in `js-projects-cdc6aae4`. The connected Vercel reader still returns 403 for that team/project. This release is not represented as live at the old production alias.

## Exact project settings

Repository: `thotsl4yer69/drifter`
Website branch: `design/field-recorder-edition` / PR #84

| Setting | Value |
| --- | --- |
| Framework | Other |
| Root Directory | `site` |
| Build Command | `node build.mjs` |
| Output Directory | `dist` |
| Install Command | Empty; no package installation |

The build settings are also in `site/vercel.json`. Root Directory remains a Vercel project setting. Do not publish the repository root or serve the source directory as the site.

Use the Vercel account/team that owns the project, connect the Git repository if needed, and select `design/field-recorder-edition` as the production branch. Create a deployment from that branch. Once READY, open the production URL shown by Vercel and verify the version as below. No merge into the deployed vehicle's `main` checkout is required just to publish this website branch.

To finish from the ChatGPT integration, reauthorize Vercel for the account/team owning this project. An existing connection without that project scope is not sufficient.

## Source and build

`build.mjs` invokes `complete-site.mjs`. The build uses only Node built-ins and the local allowlisted website files. Canonical sources are `home-source.html`, `brand-base.css`, `complete.css`, `pages.mjs`, `site.js`, `recorder-runtime.js`, `assets.mjs`, the two PDFs in `assets/`, the existing wordmark, `social-card.mjs` and `field-refinements.css`.

The generated homepage contains its own CSS and JavaScript. Other pages use the same design system. Only `dist` is deployed: legacy dashboard files, source, tests and vehicle services stay out.

From `site`:

```sh
node --test publication.test.mjs
node build.mjs
node serve.mjs
```

The preview server binds to localhost on port 8173 by default. `PORT` overrides it. To review without a server, open `dist/index.html`; links refer to the other files in the same folder.

## Domain and metadata

A custom domain is not necessary for initial publication. `VERCEL_PROJECT_PRODUCTION_URL` supplies the actual production hostname during a Vercel build. `SITE_URL` overrides it after a real custom domain has been configured. Do not substitute an unowned or speculative domain.

Portable builds without a public hostname do not invent canonical URLs or a sitemap. Production builds generate metadata and an eight-page sitemap for the selected host. Preview builds are marked noindex. The 404 document sets a site-root base, so nested missing paths can navigate home correctly.

## Verify the published build

1. The homepage shows the geometric DRIFTER wordmark and “A memory for your machine.”
2. `/version.json` reports `Field Recorder 01.2` and the Git revision when provided by Vercel.
3. The header reaches Recorder, Hardware, Field notes, Media and Apply. The footer reaches the test guide and data/contact page.
4. The recorder scrubs and jumps to the event. The report writer exports user-entered evidence. Media SVG, PNG and ZIP downloads work.
5. A nonexistent nested URL returns HTTP 404, not a homepage disguised as success.

These production checks have not been completed through the currently scope-denied Vercel connection.

## What the website does not require

No external font, runtime JavaScript package, API key, database or payment service is required. The application flow opens the visitor's email app or generates a text file. It does not claim to deliver messages itself. The website does not require access to the vehicle node.

## Build and test evidence

This release passed 21 Node publication tests, 123 local Chromium document checks and 43 local HTTP/link/archive checks. The browser checks use local HTML document rendering; they are not public-deployment or real-device verification. See `QA-SUMMARY.md` for details.
