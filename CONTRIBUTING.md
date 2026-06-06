# Contributing to DRIFTER

MZ1312 UNCAGED TECHNOLOGY — EST 1991

## Commit signing & verified history

Commits on `main` should show **Verified** on GitHub. A commit is Verified
when it carries a signature from a key GitHub trusts. There are two ways that
happens in this repo:

1. **Commits created through the GitHub API / web UI** are automatically signed
   with GitHub's web-flow GPG key and show as Verified.
2. **Locally authored commits** are Verified only if the author has configured
   GPG/SSH commit signing with a key registered to their GitHub account.

Commits pushed from an unsigned environment (e.g. an automation sandbox without
signing keys) will show as **Unverified** even when authored correctly — the
author email is not the same thing as a signature.

### Practical policy

GitHub signs the commit it *creates* during a merge, but only for some merge
methods. Verified empirically on this repo (`git log --format='%G? %ce'`):

| Merge method | Resulting commit on `main` | Signature |
|---|---|---|
| **Squash** | one new commit committed by `GitHub` | **signed → Verified** |
| **Merge commit** | a merge commit by `GitHub` (+ the PR's own commits as parents) | merge commit **signed → Verified** |
| **Rebase** | your commits *replayed* onto `main`, **not re-signed** | **unsigned → Unverified** |

- **Land changes through pull requests and merge with _Squash_ or _Create a
  merge commit_ — not _Rebase and merge_.** Rebase replays your branch commits
  without re-signing them, so anything pushed from an unsigned environment lands
  Unverified.
- **Squash** is the simplest path to a Verified `main`: one GitHub-signed commit
  per PR (authorship collapses into that commit; preserve attribution with
  `Co-authored-by:` trailers in the commit message).
- To keep **per-commit authorship _and_** Verified status, author the commits
  through the **GitHub API / web UI** (those are web-flow signed) and merge with
  **Create a merge commit** so the signed commits are preserved as parents.
- For fully verified commits authored *locally*, configure signing and register
  the key with GitHub (*Settings → SSH and GPG keys*):
  ```bash
  git config --global user.signingkey <KEY_ID>
  git config --global commit.gpgsign true   # or gpg.format=ssh for SSH signing
  ```

## Repository settings (require admin in the GitHub UI)

These cannot be set from a CLI session and must be toggled by a maintainer:

- **Allowed merge methods** — *Settings → General → Pull Requests*: keep
  *Squash merging* and/or *Merge commits* enabled; **disable _Rebase merging_**
  so a rebase can't silently land Unverified commits.
- **Require signed commits** — *Settings → Branches → Branch protection rules*
  for `main`: enable *Require signed commits* to enforce the above.

## Tests & lint

```bash
pip install -e ".[dev]"
ruff check src tests
pytest -q
```

CI runs the same `ruff` + `pytest` on every PR (`.github/workflows/deploy-drifter.yml`)
plus a daily scheduled sweep (`.github/workflows/daily-checks.yml`).
