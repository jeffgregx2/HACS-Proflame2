from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_prepopulate_esphome_deps_script_exists_and_targets_example_config() -> None:
    script = REPO_ROOT / "tools" / "prepopulate_esphome_deps.sh"
    content = script.read_text(encoding="utf-8")

    assert script.is_file()
    assert content.startswith("#!/bin/zsh")
    assert "esphome/validate_display_preset.yaml" in content
    assert "esphome config" in content
    assert "esphome compile" in content
    assert "analysis" in content
