"""Validate telemetry categories and YAML-only settling with real ESPHome schemas."""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class _Loader(yaml.SafeLoader):
    """Accept ESPHome lambda/include tags in validated YAML."""


_Loader.add_multi_constructor("!", lambda loader, tag, node: loader.construct_scalar(node))


def test_all_component_sensor_schema_defaults_are_diagnostic() -> None:
    source = ROOT / "esphome/components/proflame2_tembed/__init__.py"
    calls = [
        node
        for node in ast.walk(ast.parse(source.read_text()))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("sensor_schema", "text_sensor_schema")
    ]
    assert len(calls) == 29  # 21 base telemetry entities and eight RX debug entities.
    for call in calls:
        assert any(
            keyword.arg == "entity_category"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == "diagnostic"
            for keyword in call.keywords
        ), f"Missing diagnostic schema default at line {call.lineno}"


@pytest.fixture
def esphome_cli() -> str:
    cli = os.environ.get("ESPHOME_TEST_CLI") or shutil.which("esphome")
    if not cli:
        pytest.skip("Set ESPHOME_TEST_CLI to run the real ESPHome configuration checks")
    return cli


def _config(tmp_path: Path, *, debug: bool = False, override: int | None = None) -> Path:
    config = {
        "esphome": {"name": "lilygo-config-test"},
        "esp32": {"board": "esp32-s3-devkitc-1", "framework": {"type": "esp-idf"}},
        "logger": {},
        "ota": [{"platform": "esphome"}],
        "wifi": {"ap": {"ssid": "test-fallback"}},
        "substitutions": {"proflame2_package_ref": "main"},
        "packages": {"base": "PLACEHOLDER_BASE"},
        "external_components": [
            {
                "source": {"type": "local", "path": str(ROOT / "esphome/components")},
                "components": ["proflame2_tembed"],
            }
        ],
    }
    if debug:
        config["packages"]["debug"] = "PLACEHOLDER_DEBUG"
    if override is not None:
        config["substitutions"]["proflame2_pre_tx_settle_delay_us"] = str(override)
    text = (
        yaml.safe_dump(config)
        .replace("PLACEHOLDER_BASE", f"!include {ROOT / 'esphome/packages/proflame2_tembed_base.yaml'}")
        .replace("PLACEHOLDER_DEBUG", f"!include {ROOT / 'esphome/packages/proflame2_tembed_debug.yaml'}")
    )
    path = tmp_path / "device.yaml"
    path.write_text(text)
    return path


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("override", [None, 0, 100000])
def test_validated_entities_and_settling_configuration(
    tmp_path: Path, esphome_cli: str, debug: bool, override: int | None
) -> None:
    result = subprocess.run(
        [esphome_cli, "config", str(_config(tmp_path, debug=debug, override=override))],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    config = yaml.load(result.stdout, Loader=_Loader)
    radio = config["proflame2_tembed"]
    assert radio["pre_tx_settle_delay_us"] == (20000 if override is None else override)
    telemetry = [value for value in radio.values() if isinstance(value, dict) and "name" in value]
    assert len(telemetry) == (29 if debug else 21)
    assert all(value["entity_category"] == "diagnostic" for value in telemetry)
    assert all(not value["disabled_by_default"] for value in telemetry)
    assert all(value["name"] != "Pre-TX Settling Delay" for value in config["number"])
    trace = next(value for value in config["switch"] if value["name"] == "Radio Register Trace Logging")
    assert trace["entity_category"] == "diagnostic"
    listener = next(value for value in config["select"] if value["name"] == "Active Listener RX Path")
    assert listener["entity_category"] == "config"


@pytest.mark.parametrize("override", [-1, 100001])
def test_invalid_settling_delay_is_rejected(tmp_path: Path, esphome_cli: str, override: int) -> None:
    result = subprocess.run(
        [esphome_cli, "config", str(_config(tmp_path, override=override))],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "pre_tx_settle_delay_us" in result.stdout + result.stderr
