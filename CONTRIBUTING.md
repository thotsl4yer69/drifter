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

- **Land changes through pull requests**, and **merge with _Rebase and merge_**
  (not _Squash_). Rebase-merge replays each commit onto `main` via GitHub, so
  the commits are GitHub-signed (Verified) while preserving the original author.
- Squash merges produce a single commit committed by `GitHub`, which is signed
  (Verified on GitHub) but collapses authorship — prefer rebase when individual
  commit attribution matters.
- For fully verified *local* commits, configure signing:
  ```bash
  git config --global user.signingkey <KEY_ID>
  git config --global commit.gpgsign true   # or gpg.format=ssh for SSH signing
  ```
  and add the public key to GitHub under *Settings → SSH and GPG keys*.

## Repository settings (require admin in the GitHub UI)

These cannot be set from a CLI session and must be toggled by a maintainer:

- **Allowed merge methods** — *Settings → General → Pull Requests*: enable
  *Rebase merging* (and/or *Merge commits*); optionally disable *Squash merging*.
- **Require signed commits** — *Settings → Branches → Branch protection rules*
  for `main`: enable *Require signed commits*.

## Tests & lint

```bash
pip install -e ".[dev]"
ruff check src tests
pytest -q
```

CI runs the same `ruff` + `pytest` on every PR (`.github/workflows/deploy-drifter.yml`)
plus a daily scheduled sweep (`.github/workflows/daily-checks.yml`).
