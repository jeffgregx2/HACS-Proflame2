# DevOps Notes

This document captures project-specific release operations for maintainers.

## Release A Version

Use this flow when promoting `dev` to `main` and creating a HACS/ESPHome release.
The example below uses `v0.4.0`; replace it with the target version.

### 1. Push `dev`

```bash
git checkout dev
git status
git push origin dev
```

### 2. Stamp The Release Version

In GitHub, run the **Stamp Release Version** workflow manually:

- `version`: `0.4.0`
- `ref`: `dev`

The workflow commits the release version back to `dev` by updating:

- `custom_components/proflame2/manifest.json`
- `custom_components/proflame2/version.py`

### 3. Pull The Stamped Commit

```bash
git checkout dev
git pull origin dev
```

### 4. Open A Pull Request

Create a pull request from `dev` into `main`.

Wait for GitHub Actions to pass before merging. This validates the integration
and ESPHome firmware configuration before `main` is updated.

### 5. Merge And Sync `main`

After the PR is merged:

```bash
git checkout main
git pull origin main
```

### 6. Tag The Release

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
