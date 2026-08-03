# DevOps Notes

This document captures project-specific release operations for maintainers.

## Repository Access

Canonical GitHub repository:

- Owner/repo: `jeffgregx2/HACS-Proflame2`
- HTTPS URL: `https://github.com/jeffgregx2/HACS-Proflame2`
- Local `origin`: `git@github.com:jeffgregx2/HACS-Proflame2.git`
- Normal working branch: `dev`
- Published stable branch: `main`

For Codex sessions in this workspace, use HTTPS for read-only GitHub access.
The local git remote is SSH-backed, but Codex does not have a usable SSH key in
this environment (`git@github.com: Permission denied (publickey)`). Do not spend
time retrying SSH for issue lookup or remote reads unless credentials have been
explicitly changed.

The GitHub CLI is not installed in this workspace. Use the GitHub REST API with
`curl` for read-only issue context:

```bash
curl -L --fail https://api.github.com/repos/jeffgregx2/HACS-Proflame2/issues/11
curl -L --fail https://api.github.com/repos/jeffgregx2/HACS-Proflame2/issues/11/comments
```

For a different issue, replace only the issue number in both URLs. GitHub
attachment links in issue comments can also be fetched with `curl -L --fail`.
For read-only git refs, use HTTPS directly:

```bash
git ls-remote --heads https://github.com/jeffgregx2/HACS-Proflame2.git dev main
```

Do not store GitHub tokens, credentials, or one-off authenticated URLs in this
repository. If a workflow requires authenticated GitHub writes, stop and confirm
the intended credential path instead of guessing. Prefer GitHub Actions for
release operations.

## Python Environment

Use the repository virtual environment for Python commands:

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/ruff check custom_components tools tests
./.venv/bin/black --check custom_components tools tests
```

The Makefile already defaults `PYTHON`, `RUFF`, and `BLACK` to `./.venv/bin/*`,
so `make test`, `make lint-python`, and `make format-python-check` are safe
entrypoints.

ESPHome validation uses a separate virtual environment:

```bash
make esphome-validate
```

That target creates/uses `./.venv-esphome/bin/python` and installs
`requirements-esphome.txt` as needed.

## Release A Version

Use this flow when promoting `dev` to `main` and creating a HACS/ESPHome release.
The example below uses `v0.4.0`; replace it with the target version.

### 1. Stamp Documentation Links For `dev`

Before pushing a promoted branch, stamp repository documentation links to the
branch that will contain the documentation:

```bash
./.venv/bin/python scripts/stamp_docs_ref.py --ref dev
git diff -- custom_components/proflame2/manifest.json custom_components/proflame2/docs_urls.py
```

This keeps Home Assistant help links and config-flow guide links resolvable from
the branch being tested. The links will resolve on GitHub after the stamped
branch content is pushed.

Pushes to any branch also run the **Documentation Links** workflow. The workflow
stamps and commits the correct branch ref automatically if a push left the links
pointing somewhere else. Pull requests validate that links point at the source
branch, so branch docs can be tested before merging.

### 2. Push `dev`

```bash
git checkout dev
git status
git push origin dev
```

### 3. Stamp The Release Version

In GitHub, run the **Stamp Release Version** workflow manually:

- `version`: `0.4.0`
- `ref`: `dev`

The workflow commits the release version and branch documentation ref back to
`dev` by updating:

- `custom_components/proflame2/manifest.json`
- `custom_components/proflame2/version.py`
- `custom_components/proflame2/docs_urls.py`

The documentation URL still points at the branch being stamped. For a normal
release, the final tag is created from `main`, and release validation requires
the tag contents to point at `main`.

### 4. Pull The Stamped Commit

```bash
git checkout dev
git pull origin dev
```

### 5. Open A Pull Request

Create a pull request from `dev` into `main`.

Wait for GitHub Actions to pass before merging. This validates the integration
and ESPHome firmware configuration before `main` is updated.

### 6. Merge And Sync `main`

After the PR is merged:

```bash
git checkout main
git pull origin main
```

Wait for the **Documentation Links** workflow on `main` to finish. If it stamped
the documentation links to `main`, pull that commit before tagging:

```bash
git pull origin main
```

### 7. Tag The Release

Create the tag from the merged, version-stamped `main` commit:

```bash
git tag -a v0.4.0 -m "Release v0.4.0"
git push origin v0.4.0
```

Do not tag before the version-stamp commit is merged to `main`. The release tag
must point at the exact commit users should install.

### 7. Create The GitHub Release

In GitHub, create a release from tag `v0.4.0`.

HACS uses the GitHub release as the installable integration release. ESPHome
users can pin package references to the same tag, for example `v0.4.0`, when
they want reproducible firmware builds.
