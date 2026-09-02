"""Tests for repository documentation URL generation."""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path

from custom_components.proflame2 import docs_urls


def _load_stamp_docs_ref_module():
    spec = importlib.util.spec_from_file_location("stamp_docs_ref", "scripts/stamp_docs_ref.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load scripts/stamp_docs_ref.py")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_documentation_url_uses_default_ref() -> None:
    """Documentation links should resolve through the configured default ref."""

    assert docs_urls.add_fireplace_profile_url() == (
        "https://github.com/jeffgregx2/HACS-Proflame2"
        f"/blob/{docs_urls.DEFAULT_DOCUMENTATION_REF}/"
        "docs/add_fireplace_profile.md#add-fireplace-profile-options"
    )
    assert docs_urls.lilygo_controller_url().endswith(
        f"/blob/{docs_urls.DEFAULT_DOCUMENTATION_REF}/docs/lilygo_cc1101_controller.md"
    )
    assert docs_urls.rtl433_manual_learning_url().endswith(
        f"/blob/{docs_urls.DEFAULT_DOCUMENTATION_REF}/docs/rtl433_manual_learning.md"
    )


def test_documentation_url_supports_env_override(monkeypatch) -> None:
    """Local and test runs may override the documentation ref without code changes."""

    monkeypatch.setenv(docs_urls.DOCUMENTATION_REF_ENV, "dev")

    assert docs_urls.documentation_url("docs/example.md", "section") == (
        "https://github.com/jeffgregx2/HACS-Proflame2/blob/dev/docs/example.md#section"
    )


def test_manifest_documentation_matches_default_add_profile_url(monkeypatch) -> None:
    """The Home Assistant manifest help link should use the same default docs ref."""

    monkeypatch.delenv(docs_urls.DOCUMENTATION_REF_ENV, raising=False)
    importlib.reload(docs_urls)
    manifest = json.loads(Path("custom_components/proflame2/manifest.json").read_text(encoding="utf-8"))

    assert manifest["documentation"] == docs_urls.add_fireplace_profile_url()


def test_stamp_script_uses_same_manifest_documentation_url() -> None:
    """The branch/release stamping script should share the runtime URL convention."""

    stamp_docs_ref = _load_stamp_docs_ref_module()

    assert stamp_docs_ref.documentation_url("dev") == (
        "https://github.com/jeffgregx2/HACS-Proflame2"
        "/blob/dev/docs/add_fireplace_profile.md#add-fireplace-profile-options"
    )


def test_stamp_script_supports_immutable_release_tags() -> None:
    """Release links may target the tag that contains the shipped docs."""

    stamp_docs_ref = _load_stamp_docs_ref_module()

    assert stamp_docs_ref.documentation_url("v0.6.0-beta1") == (
        "https://github.com/jeffgregx2/HACS-Proflame2"
        "/blob/v0.6.0-beta1/docs/add_fireplace_profile.md#add-fireplace-profile-options"
    )
