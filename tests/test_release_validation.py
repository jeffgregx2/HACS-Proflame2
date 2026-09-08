"""Tests for the release-source validation and release-tag automation."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_release_validation_module():
    spec = importlib.util.spec_from_file_location(
        "validate_release_source", REPO_ROOT / "scripts/validate_release_source.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load scripts/validate_release_source.py")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_beta_release_source_is_self_consistent() -> None:
    """The checked-out beta source should pass the same pre-tag validation."""

    release_validation = _load_release_validation_module()
    manifest = json.loads((REPO_ROOT / "custom_components/proflame2/manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    release_validation.validate_release_source(f"v{version}", prerelease="-beta" in version)


@pytest.mark.parametrize("tag", ("0.6.0", "v0.6", "v0.6.0-rc1", "v0.6.0-beta"))
def test_release_tag_parser_rejects_unsupported_tags(tag: str) -> None:
    """Only the documented release tag formats are accepted."""

    release_validation = _load_release_validation_module()

    with pytest.raises(ValueError, match="Invalid release tag"):
        release_validation.parse_release_tag(tag)


def test_beta_tag_requires_prerelease_flag() -> None:
    """A beta tag cannot be published as a non-prerelease by accident."""

    release_validation = _load_release_validation_module()

    with pytest.raises(ValueError, match="must be marked as a GitHub prerelease"):
        release_validation.validate_release_source("v0.6.0-beta3", prerelease=False)


def test_release_source_rejects_an_unstamped_manifest(tmp_path: Path, monkeypatch) -> None:
    """The preparation action must catch version drift before it creates a tag."""

    manifest_path = tmp_path / "custom_components/proflame2/manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({"version": "0.6.0-beta2"}), encoding="utf-8")

    version_path = tmp_path / "custom_components/proflame2/version.py"
    version_path.write_text('DEFAULT_INTEGRATION_VERSION = "0.6.0-beta3"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    release_validation = _load_release_validation_module()

    with pytest.raises(ValueError, match="manifest.json version mismatch"):
        release_validation.validate_release_source("v0.6.0-beta3")


def test_release_source_rejects_branch_documentation_links(tmp_path: Path, monkeypatch) -> None:
    """A future release tag must not publish links that still point at a branch."""

    package_path = tmp_path / "custom_components/proflame2"
    package_path.mkdir(parents=True)
    (package_path / "manifest.json").write_text(
        json.dumps(
            {
                "version": "0.6.0-beta3",
                "documentation": (
                    "https://github.com/jeffgregx2/HACS-Proflame2/blob/dev/"
                    "docs/add_fireplace_profile.md#add-fireplace-profile-options"
                ),
            }
        ),
        encoding="utf-8",
    )
    (package_path / "version.py").write_text('DEFAULT_INTEGRATION_VERSION = "0.6.0-beta3"\n', encoding="utf-8")
    (package_path / "docs_urls.py").write_text(
        "\n".join(
            (
                'REPOSITORY_URL = "https://github.com/jeffgregx2/HACS-Proflame2"',
                'ADD_FIREPLACE_PROFILE_DOC = "docs/add_fireplace_profile.md"',
                'ADD_FIREPLACE_PROFILE_OPTIONS_ANCHOR = "add-fireplace-profile-options"',
                'DEFAULT_DOCUMENTATION_REF = "dev"',
                "",
            )
        ),
        encoding="utf-8",
    )
    firmware_package_path = tmp_path / "esphome/packages/proflame2_tembed_base.yaml"
    firmware_package_path.parent.mkdir(parents=True)
    firmware_package_path.write_text(
        '  proflame2_firmware_version: "v0.6.0-beta3"\n',
        encoding="utf-8",
    )
    scripts_path = tmp_path / "scripts"
    scripts_path.mkdir()
    (scripts_path / "stamp_docs_ref.py").write_text(
        (REPO_ROOT / "scripts/stamp_docs_ref.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    release_validation = _load_release_validation_module()

    with pytest.raises(SystemExit, match="documentation URL mismatch"):
        release_validation.validate_release_source("v0.6.0-beta3")


def test_release_workflows_validate_before_creating_or_publishing_tags() -> None:
    """The automated release path should own both preflight and tag creation."""

    stamp_workflow = (REPO_ROOT / ".github/workflows/release-version.yml").read_text(encoding="utf-8")
    validation_workflow = (REPO_ROOT / ".github/workflows/release-validation.yml").read_text(encoding="utf-8")

    assert "replace_existing_tag:" in stamp_workflow
    assert "confirm_replace_tag:" in stamp_workflow
    assert "Validate release source before tagging" in stamp_workflow
    assert "Create immutable release tag" in stamp_workflow
    assert "proflame2_firmware_version" in stamp_workflow
    assert "esphome/packages/proflame2_tembed_base.yaml" in stamp_workflow
    assert "gh api --include" in stamp_workflow
    assert "refusing to move it" in stamp_workflow
    assert 'git push origin "HEAD:refs/heads/$RELEASE_REF" "refs/tags/$RELEASE_TAG"' in stamp_workflow
    assert "scripts/validate_release_source.py" in validation_workflow
