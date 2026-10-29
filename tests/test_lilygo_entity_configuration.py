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


def _config(
    tmp_path: Path,
    *,
    debug: bool = False,
    override: int | None = None,
    diagnostics: bool | None = None,
    frequency_offset: int | None = None,
    band: str = "315",
) -> Path:
    # Preserve ESPHome tags while removing the production remote source from this
    # temporary package. A second external_components list appends, not replaces.
    base_node = yaml.compose((ROOT / "esphome/packages/proflame2_tembed_base.yaml").read_text())
    assert isinstance(base_node, yaml.MappingNode)
    base_node.value = [(key, value) for key, value in base_node.value if key.value != "external_components"]
    local_base = tmp_path / "base_local.yaml"
    local_base.write_text(yaml.serialize(base_node))
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
    config["substitutions"]["proflame2_rf_band"] = band
    if frequency_offset is not None:
        config["substitutions"]["proflame2_tx_frequency_offset_hz"] = str(frequency_offset)
    if diagnostics is not None:
        config["proflame2_tembed"] = {"waveform_diagnostics": diagnostics}
        if diagnostics:
            config["packages"]["waveform"] = "PLACEHOLDER_WAVEFORM"
    text = (
        yaml.safe_dump(config)
        .replace("PLACEHOLDER_BASE", f"!include {local_base}")
        .replace("PLACEHOLDER_DEBUG", f"!include {ROOT / 'esphome/packages/proflame2_tembed_debug.yaml'}")
        .replace(
            "PLACEHOLDER_WAVEFORM", f"!include {ROOT / 'esphome/packages/proflame2_tembed_waveform_diagnostics.yaml'}"
        )
    )
    path = tmp_path / "device.yaml"
    path.write_text(text)
    return path


@pytest.mark.parametrize("enabled", [False, True])
def test_waveform_diagnostics_codegen_is_explicit_opt_in(tmp_path, esphome_cli, enabled):
    config_path = _config(tmp_path, diagnostics=enabled)
    if enabled:
        config_path.write_text(
            config_path.read_text().replace(
                "waveform_diagnostics: true", "waveform_diagnostics: true\n  waveform_session_ms: 90000"
            )
        )
    result = subprocess.run(
        [esphome_cli, "compile", "--only-generate", str(config_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    generated = list(tmp_path.rglob("defines.h"))
    assert generated
    defines = "\n".join(path.read_text() for path in generated)
    assert ("#define USE_PROFLAME2_WAVEFORM_DIAGNOSTICS" in defines) is enabled
    assert ("#define USE_PROFLAME2_BUFFERED_WAVEFORM_DIAGNOSTICS" in defines) is enabled
    assert "#define USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS" not in defines
    main = "\n".join(path.read_text() for path in tmp_path.rglob("main.cpp"))
    assert ("proflame2_waveform_start" in main) is enabled
    assert ("start_waveform_capture()" in main) is enabled
    base_version = yaml.load((ROOT / "esphome/packages/proflame2_tembed_base.yaml").read_text(), Loader=_Loader)[
        "substitutions"
    ]["proflame2_firmware_version"]
    expected_version = "v0.7.0-debug" if enabled else base_version
    assert f'set_firmware_version("{expected_version}")' in main
    if enabled:
        assert "90000" in main
        for name in ("Waveform Capture Start", "Waveform Capture Stop"):
            assert name in main
        validated = subprocess.run(
            [esphome_cli, "config", str(config_path)], capture_output=True, text=True, timeout=120
        )
        assert validated.returncode == 0, validated.stdout + validated.stderr
        config = yaml.load(validated.stdout, Loader=_Loader)
        buttons = {item["name"]: item for item in config["button"]}
        for name in ("Waveform Capture Start", "Waveform Capture Stop"):
            assert buttons[name].get("entity_category") is None
            assert buttons[name]["disabled_by_default"] is False


def test_waveform_diagnostics_rejects_unsupported_framework(tmp_path, esphome_cli):
    config_path = _config(tmp_path, diagnostics=True)
    config_path.write_text(config_path.read_text().replace("type: esp-idf", "type: arduino"))
    result = subprocess.run([esphome_cli, "config", str(config_path)], capture_output=True, text=True, timeout=120)
    assert result.returncode != 0
    assert "Waveform diagnostics requires the esp-idf framework" in result.stdout + result.stderr


@pytest.mark.parametrize("diagnostics", [False, True])
def test_gpio_edge_observer_requires_explicit_waveform_build(tmp_path, esphome_cli, diagnostics):
    path = _config(tmp_path, diagnostics=diagnostics)
    path.write_text(
        path.read_text().replace(
            f"waveform_diagnostics: {str(diagnostics).lower()}",
            f"waveform_diagnostics: {str(diagnostics).lower()}\n  waveform_gpio_edge_observer: true",
        )
    )
    result = subprocess.run(
        [esphome_cli, "compile", "--only-generate", str(path)], capture_output=True, text=True, timeout=120
    )
    if not diagnostics:
        assert result.returncode != 0
        assert "waveform_gpio_edge_observer requires waveform_diagnostics" in result.stdout + result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert any(
            "#define USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS" in item.read_text() for item in tmp_path.rglob("defines.h")
        )


def test_diagnostic_session_ignores_normal_rx_stop_and_capture_controls():
    source = (ROOT / "esphome/components/proflame2_tembed/proflame2_tembed.cpp").read_text()
    mode = source.split("void Proflame2TEmbedComponent::set_capture_mode(", 1)[1]
    guard = mode.split("#endif", 1)[0]
    assert "#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS" in guard
    assert "return;" in guard
    assert 'value != "off"' not in guard
    header = (ROOT / "esphome/components/proflame2_tembed/proflame2_tembed.h").read_text()
    controls = header.split("void start_waveform_capture()", 1)[1].split("void set_waveform_diagnostic_limits", 1)[0]
    assert "set_capture_mode" not in controls
    assert "set_rmt_pulse_capture_enabled_(true)" in controls
    assert "this->rmt_ook_receiver_.end();" in controls


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
    assert radio["diagnostic_repeat_count_override"] == 10
    assert radio["tx_repeat_count"] == 5  # Preserve the integration's request contract.
    assert radio["tx_frequency_offset_hz"] == 0
    telemetry = [value for value in radio.values() if isinstance(value, dict) and "name" in value]
    assert len(telemetry) == (29 if debug else 21)
    assert all(value["entity_category"] == "diagnostic" for value in telemetry)
    assert all(not value["disabled_by_default"] for value in telemetry)
    assert all(value["name"] != "Pre-TX Settling Delay" for value in config["number"])
    trace = next(value for value in config["switch"] if value["name"] == "Radio Register Trace Logging")
    assert trace["entity_category"] == "diagnostic"
    retry = next(value for value in config["switch"] if value["name"] == "Delayed Retransmission")
    assert retry["restore_mode"] == "ALWAYS_OFF"
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


@pytest.mark.parametrize("band", ["315", "433"])
@pytest.mark.parametrize("offset", [None, 60000, -60000, 100000, -100000])
def test_tx_only_frequency_offset_codegen(tmp_path, esphome_cli, band, offset):
    path = _config(tmp_path, frequency_offset=offset, band=band)
    result = subprocess.run(
        [esphome_cli, "compile", "--only-generate", str(path)], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr
    main = "\n".join(p.read_text() for p in tmp_path.rglob("main.cpp"))
    assert f"set_tx_frequency_offset_hz({offset if offset is not None else 0})" in main


@pytest.mark.parametrize("offset", [-100001, 100001, 2147483647, -2147483648])
def test_invalid_tx_frequency_offset_rejected(tmp_path, esphome_cli, offset):
    path = _config(tmp_path, frequency_offset=offset)
    result = subprocess.run([esphome_cli, "config", str(path)], capture_output=True, text=True, timeout=120)
    assert result.returncode != 0
    assert "tx_frequency_offset_hz" in result.stdout + result.stderr
