"""Tests for ESPHome firmware source-tree scaffolding."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ESPHOME_ROOT = REPO_ROOT / "esphome"


def _read(path: str) -> str:
    return (ESPHOME_ROOT / path).read_text(encoding="utf-8")


def _yaml_entity_block(text: str, entity_id: str) -> str:
    marker = f"    id: {entity_id}"
    start = text.index(marker)
    next_entity = text.find("\n  - platform:", start + len(marker))
    if next_entity == -1:
        return text[start:]
    return text[start:next_entity]


def test_esphome_firmware_tree_contains_expected_source_files() -> None:
    expected = {
        "README.md",
        "examples/lilygo_cc1101_example.yaml",
        "packages/proflame2_tembed_base.yaml",
        "packages/proflame2_tembed_display.yaml",
        "packages/proflame2_tembed_display_lvgl.yaml",
        "components/proflame2_tembed/__init__.py",
        "components/proflame2_tembed/active_listener.h",
        "components/proflame2_tembed/active_listener.cpp",
        "components/proflame2_tembed/battery_monitor.h",
        "components/proflame2_tembed/battery_monitor.cpp",
        "components/proflame2_tembed/display_controller.h",
        "components/proflame2_tembed/display_controller.cpp",
        "components/proflame2_tembed/fifo_rx_controller.h",
        "components/proflame2_tembed/fifo_rx_controller.cpp",
        "components/proflame2_tembed/proflame2_tembed.h",
        "components/proflame2_tembed/proflame2_tembed.cpp",
        "components/proflame2_tembed/proflame2_decoder.h",
        "components/proflame2_tembed/proflame2_decoder.cpp",
        "components/proflame2_tembed/radio_cc1101.h",
        "components/proflame2_tembed/radio_cc1101.cpp",
        "components/proflame2_tembed/radio_cc1101_rx.cpp",
        "components/proflame2_tembed/radio_cc1101_tx.cpp",
        "components/proflame2_tembed/telemetry_publisher.h",
        "components/proflame2_tembed/telemetry_publisher.cpp",
        "components/proflame2_tembed/tx_controller.h",
        "components/proflame2_tembed/tx_controller.cpp",
        "components/proflame2_tembed/display_state.h",
        "components/proflame2_tembed/display_state.cpp",
    }

    assert expected.issubset(
        {str(path.relative_to(ESPHOME_ROOT)) for path in ESPHOME_ROOT.rglob("*") if path.is_file()}
    )


def test_esphome_tembed_decomposition_docs_match_current_shell_layout() -> None:
    header = _read("components/proflame2_tembed/proflame2_tembed.h")
    plan = (REPO_ROOT / "docs/proflame2_tembed_decomposition_plan.md").read_text(encoding="utf-8")

    assert "ESPHome integration shell" in header
    assert "`BatteryMonitor` owns PMIC/battery reads." in header
    assert "`DisplayController` owns display text/policy formatting." in header
    assert "`TelemetryPublisher` owns publish-if-changed helpers." in header
    assert "`FifoRxController` owns bounded FIFO byte-window storage." in header
    assert "`ActiveListenerController` owns learned-profile packet acceptance policy." in header
    assert "`TxController` owns TX payload validation and transport-shape policy." in header

    assert "# Proflame2 T-Embed Decomposition Status" in plan
    assert "## Current Boundaries" in plan
    assert "## Completed Split Order" in plan
    assert "Active RX is FIFO semantic capture only." in " ".join(plan.split())
    assert "GDO edge-interval ownership path remains out of scope" in plan


def test_esphome_external_component_uses_codegen_schema_and_action() -> None:
    component = _read("components/proflame2_tembed/__init__.py")

    assert "CONFIG_SCHEMA" in component
    assert "cv.Schema" in component
    assert "cg.new_Pvariable" in component
    assert 'DEPENDENCIES = ["spi", "i2c", "api"]' in component
    assert "pins.gpio_output_pin_schema" in component
    assert "pins.gpio_input_pin_schema" not in component
    assert component.count("pins.gpio_output_pin_schema") >= 2
    assert "automation.register_action" in component
    assert '"proflame2_tembed.tx"' in component
    assert '"proflame2_tembed.cc1101_test_pattern"' in component
    assert "custom_component" not in component
    assert "lambda:" not in component


def test_esphome_yaml_wires_local_external_component_and_tx_api() -> None:
    example = _read("examples/lilygo_cc1101_example.yaml")
    base = _read("packages/proflame2_tembed_base.yaml")

    assert "LilyGO T-Embed CC1101 Proflame2 ESPHome example" in example
    assert "type: local" in base
    assert "path: components" in base
    assert "components: [proflame2_tembed]" in base
    assert "platform: wifi_signal" in base
    assert "on_connect:" in base
    assert "on_disconnect:" in base
    assert "homeassistant_services: true" in base
    assert "on_client_connected:" in base
    assert "on_client_disconnected:" in base
    assert "handle_api_client_connected(client_info)" in base
    assert "handle_api_client_disconnected(client_info)" in base
    assert "set_api_connected(true)" not in base
    assert "set_api_connected(false)" not in base
    assert "proflame2_tembed:" in base
    assert "id: proflame2_radio" in base
    assert 'proflame2_payload_bit_length_override: "182"' in base
    assert "payload_bit_length_override: ${proflame2_payload_bit_length_override}" in base
    assert 'proflame2_tx_mode_override: "proflame_native_groups"' in base
    assert 'proflame2_native_group_timing_profile_override: "native_remote"' in base
    assert "tx_mode: ${proflame2_tx_mode_override}" in base
    assert "native_group_timing_profile: ${proflame2_native_group_timing_profile_override}" in base
    assert 'proflame2_pre_burst_low_us_override: "834"' in base
    assert 'proflame2_pre_frame_low_us_override: "0"' in base
    assert 'proflame2_diagnostic_repeat_count_override: "5"' in base
    assert "pre_burst_low_us: ${proflame2_pre_burst_low_us_override}" in base
    assert "pre_frame_low_us: ${proflame2_pre_frame_low_us_override}" in base
    assert "diagnostic_repeat_count_override: ${proflame2_diagnostic_repeat_count_override}" in base
    assert "name: Display Dim Level" in base
    assert "globals:" in base
    assert "id: proflame2_display_dim_level_state" in base
    assert "id: proflame2_display_dim_timeout_state" in base
    assert "id: proflame2_display_wake_on_activity_state" in base
    assert "id: proflame2_display_short_name_state" in base
    assert "restore_value: yes" in base
    assert "min_value: 0" in base
    assert "max_value: 10" in base
    assert 'initial_value: "3"' in base
    assert "name: Display Dim Timeout" in base
    assert 'initial_value: "1"' in base
    assert "select:" in base
    assert "platform: template" in base
    assert "options:" in base
    assert '      - "0"' in base
    assert '      - "60"' in base
    assert "return to_string(id(proflame2_display_dim_timeout_state));" in base
    assert "name: Display Wake On Activity" in base
    assert "restore_mode: RESTORE_DEFAULT_ON" in base
    assert 'initial_value: "true"' in base
    assert "lambda: |-" in base
    assert "set_display_dim_level" in base
    assert "set_display_dim_timeout_min" in base
    assert "set_display_wake_on_activity" in base
    assert "set_display_fireplace_name" in base
    assert "if (id(proflame2_display_dim_level_state) != value)" in base
    assert "if (id(proflame2_display_dim_timeout_state) != value)" in base
    assert "if (!id(proflame2_display_wake_on_activity_state))" in base
    assert "if (id(proflame2_display_wake_on_activity_state))" in base
    assert "id(proflame2_display_short_name_state) != fireplace_name" in base
    assert "display_dim_timeout_min:" not in base
    assert "display_wake_on_activity:" not in base
    assert "action: proflame2_tx" in base
    assert "action: proflame2_cc1101_test_pattern" in base
    assert "request_id: string" in base
    assert "air_payload_hex: string" in base
    assert "payload_bit_length: int" in base
    assert "repeat_count: int" in base
    assert "intended_power: int" in base
    assert "proflame2_display_state_update" in base
    assert "intended_flame: int" in base
    assert "intended_front: int" in base
    assert "intended_action_label: string" in base
    assert "battery_percent:" in base
    assert "internal: false" in base
    assert "proflame2_tembed.tx:" in base
    assert "proflame2_tembed.tx_stateful:" in base
    assert "proflame2_tembed.display_state_update:" in base
    assert "proflame2_tembed.cc1101_test_pattern:" in base
    assert 'proflame2_inter_frame_gap_us_override: "0"' in base
    assert "ignore_strapping_warning: true" in base
    assert "rx_enabled:" not in base
    assert "last_rx_payload_hex:" not in base
    assert "rx_packet_count:" not in base
    assert "last_rx_rssi:" not in base
    assert "last_rx_lqi:" not in base
    assert "proflame2_rx_set_active_listening" in base
    assert "serial_id: int" in base
    assert "configure_active_listener(" in base
    assert "proflame2_rx_stop" in base
    assert "proflame2_learn_mode_update" in base
    assert "set_learn_mode(" in base
    assert "update_interval: 250ms" not in base
    assert "endpoint_status:" in base
    assert "last_tx_result:" in base
    assert "tx_success_count:" in base
    assert "TX, guided FIFO learning, and FIFO active listening" in base
    assert "FIFO active listening" in base
    assert "homeassistant.event" not in base
    for removed_edge_control in (
        "proflame2_rx_polarity_mode",
        "proflame2_rx_level_assignment_mode",
        "proflame2_rx_demod_profile",
        "proflame2_rx_saturated_gap_mode",
        "proflame2_rx_tiny_pulse_mode",
        "proflame2_rx_capture_profile",
        "proflame2_rx_always_listen_debug",
        "proflame2_rx_cs_holdoff_ms",
    ):
        assert removed_edge_control not in base
    assert 'id(proflame2_radio).set_capture_mode("off");' in base
    assert "rx_dropped_packet_count:" in base
    assert "name: Proflame2 RX Dropped Packets" in base
    assert "rx_no_rf_capture_count:" not in base
    assert "rx_last_rejection_snapshot:" not in base


def test_esphome_hides_legacy_rx_debug_controls_from_normal_ui() -> None:
    base = _read("packages/proflame2_tembed_base.yaml")
    debug = _read("packages/proflame2_tembed_debug.yaml")

    removed_edge_entities = [
        "proflame2_rx_always_listen_debug",
        "proflame2_rx_qualification_mode",
        "proflame2_rx_polarity_mode",
        "proflame2_rx_capture_profile",
        "proflame2_rx_level_assignment_mode",
        "proflame2_rx_demod_profile",
        "proflame2_rx_saturated_gap_mode",
        "proflame2_rx_tiny_pulse_mode",
    ]
    for entity_id in removed_edge_entities:
        assert entity_id not in base

    diagnostic_entities = [
        "proflame2_enable_capture",
        "proflame2_rx_fifo_profile",
        "proflame2_rx_fifo_probe",
        "proflame2_rx_fifo_capture_complete",
    ]
    for entity_id in diagnostic_entities:
        assert entity_id not in base
        assert entity_id in debug


def test_esphome_debug_package_restores_fifo_diagnostics_and_debug_define() -> None:
    debug = _read("packages/proflame2_tembed_debug.yaml")

    assert "-DPROFLAME2_TEMBED_DEBUG=1" in debug
    assert "id: proflame2_enable_capture" in debug
    assert "name: Enable Capture" in debug
    assert '      - "off"' in debug
    assert '      - "fifo_trailing_window"' in debug
    assert "id(proflame2_radio).set_capture_mode(x);" in debug
    assert "id: proflame2_rx_fifo_profile_state" in debug
    assert "initial_value: '\"rfcat_fixed_none_rfcat_wide\"'" in debug
    assert "id: proflame2_rx_fifo_profile" in debug
    assert "name: RX FIFO Profile" in debug
    assert '      - "rfcat_fixed_none_rfcat_defaults"' in debug
    assert '      - "rfcat_infinite_none_rfcat_defaults"' in debug
    assert '      - "rfcat_fixed_none_rfcat_wide"' in debug
    assert '      - "rfcat_infinite_none_rfcat_wide"' in debug
    assert '      - "rfcat_infinite_carrier"' in debug
    assert '      - "rfcat_fixed_carrier"' in debug
    assert '      - "rfcat_infinite_none"' in debug
    assert '      - "rfcat_fixed_none"' in debug
    assert "id(proflame2_radio).set_rx_fifo_profile(id(proflame2_rx_fifo_profile_state));" in debug
    assert "id(proflame2_radio).set_rx_fifo_profile(x);" in debug
    assert "button:" in debug
    assert "id: proflame2_rx_fifo_probe" in debug
    assert "id(proflame2_radio).run_rx_fifo_probe();" in debug
    assert "id: proflame2_rx_fifo_capture_complete" in debug
    assert "name: RX FIFO Capture Complete" in debug
    assert "id(proflame2_radio).complete_rx_fifo_capture();" in debug
    assert "rx_no_rf_capture_count:" in debug
    assert "rx_incomplete_fifo_count:" in debug
    assert "rx_decode_failed_count:" in debug
    assert "rx_profile_mismatch_count:" in debug
    assert "rx_accepted_packet_count:" in debug
    assert "rx_tx_suppressed_count:" in debug
    assert "rx_transport_unavailable_count:" in debug
    assert "rx_last_rejection_snapshot:" in debug


def test_esphome_fifo_profile_tuning_is_wired_and_exported() -> None:
    header = _read("components/proflame2_tembed/proflame2_tembed.h")
    impl = _read("components/proflame2_tembed/proflame2_tembed.cpp")
    debug = _read("packages/proflame2_tembed_debug.yaml")

    assert "#define PROFLAME2_TEMBED_DEBUG 0" in header
    assert 'std::string rx_fifo_profile_{"rfcat_fixed_none_rfcat_wide"};' in header
    assert "void set_rx_fifo_profile(const std::string& value);" in header
    assert 'value == "rfcat_fixed_none_rfcat_defaults"' in impl
    assert 'value == "rfcat_infinite_none_rfcat_defaults"' in impl
    assert 'value == "rfcat_fixed_none_rfcat_wide"' in impl
    assert 'value == "rfcat_infinite_none_rfcat_wide"' in impl
    assert 'value == "rfcat_fixed_none"' in impl
    assert 'value == "rfcat_infinite_none"' in impl
    assert 'value == "rfcat_fixed_carrier"' in impl
    assert 'value == "rfcat_infinite_carrier"' in impl
    assert "const bool infinite_mode =" in impl
    assert "const bool carrier_gated =" in impl
    assert "const bool rfcat_defaults =" in impl
    assert "const bool wide_bandwidth =" in impl
    assert "const uint8_t pktctrl0 = infinite_mode ? 0x02 : 0x00;" in impl
    assert "const uint8_t sync_mode = carrier_gated ? 0x04 : 0x00;" in impl
    assert "const uint8_t agcctrl2 = rfcat_defaults ? 0x03 : 0x43;" in impl
    assert "const uint8_t frend0 = rfcat_defaults ? 0x10 : 0x11;" in impl
    assert "const uint8_t mdmcfg4_effective = wide_bandwidth ?" in impl
    assert "this->write_register_(CC1101_PKTCTRL0, pktctrl0);" in impl
    assert "this->write_register_(CC1101_MDMCFG4, mdmcfg4_effective);" in impl
    assert "this->write_register_(CC1101_MDMCFG2, static_cast<uint8_t>(0x30 | sync_mode));" in impl
    assert "this->write_register_(CC1101_AGCCTRL2, agcctrl2);" in impl
    assert "this->write_register_(CC1101_FREND0, frend0);" in impl
    assert "profile=%s frequency_hz=%" in impl
    assert "RX fifo probe meta radio_regs1 schema=2" in impl
    assert "RX fifo probe meta radio_regs2 schema=2" in impl
    assert "id(proflame2_radio).set_rx_fifo_profile(id(proflame2_rx_fifo_profile_state));" in debug
    assert "id(proflame2_radio).set_rx_fifo_profile(x);" in debug


def test_esphome_display_debug_mode_is_wired_from_yaml_into_component() -> None:
    component = _read("components/proflame2_tembed/__init__.py")
    header = _read("components/proflame2_tembed/proflame2_tembed.h")
    base = _read("packages/proflame2_tembed_base.yaml")

    assert 'CONF_DISPLAY_DEBUG_MODE = "display_debug_mode"' in component
    assert "cv.Optional(CONF_DISPLAY_DEBUG_MODE, default=False): cv.boolean" in component
    assert "cg.add(var.set_display_debug_mode(config[CONF_DISPLAY_DEBUG_MODE]))" in component
    assert 'proflame2_display_debug_mode: "false"' in base
    assert "display_debug_mode: ${proflame2_display_debug_mode}" in base
    assert "void set_display_debug_mode(bool value)" in header
    assert "this->display_.display_refresh_pending = true;" in header


def test_esphome_rx_edge_debug_mode_is_removed_from_component_schema() -> None:
    component = _read("components/proflame2_tembed/__init__.py")

    assert "rx_debug_mode" not in component
    assert "rx_deep_debug_mode" not in component


def test_esphome_display_package_uses_lvgl_mipi_spi_and_deferred_refresh() -> None:
    display = _read("packages/proflame2_tembed_display.yaml")
    display_lvgl = _read("packages/proflame2_tembed_display_lvgl.yaml")

    assert "psram:" in display
    assert "platform: mipi_spi" in display
    assert "model: T-EMBED" in display
    assert "cs_pin: GPIO41" in display
    assert "dc_pin: GPIO16" in display
    assert "reset_pin: GPIO40" in display
    assert "pin: GPIO21" in display
    assert "platform: ledc" in display
    assert "proflame2_display_backlight_pwm" in display
    assert "output.set_level:" in display
    assert "number: GPIO0" in display
    assert "ignore_strapping_warning: true" in display
    assert "delayed_on: 20ms" in display
    assert "delayed_off: 20ms" in display
    assert "handle_center_button_press();" in display
    assert "data_rate: 40MHz" in display
    assert "invert_colors: true" in display
    assert "dimensions:" not in display
    assert "offset_width:" not in display
    assert "offset_height:" not in display
    assert "transform:" not in display
    assert "swap_xy:" not in display
    assert "mirror_x:" not in display
    assert "mirror_y:" not in display
    assert "rotation:" not in display
    assert "auto_clear_enabled: false" in display
    assert "update_interval: never" in display
    assert "interval:" in display
    assert "is_display_update_allowed()" in display
    assert "display_refresh_pending()" in display
    assert "mark_display_refresh_applied()" in display
    assert "display_backlight_refresh_pending()" in display
    assert "mark_display_backlight_refresh_applied()" in display
    assert "refresh apply mode=%s" in display
    assert "refresh skip busy" in display
    assert "lvgl:" in display_lvgl
    assert "rotation: 270" in display_lvgl
    assert 'proflame2_display_short_name: "---"' in display
    assert "file: mdi:battery-outline" in display
    assert "file: mdi:wifi" in display
    assert "file: mdi:home-assistant" in display
    assert "id: proflame2_header_battery_icon" in display
    assert "pad_top: 3" in display_lvgl
    assert "proflame2_ui_header" in display_lvgl
    assert "proflame2_header_battery_value" in display_lvgl
    assert "proflame2_header_wifi_value" in display_lvgl
    assert "proflame2_header_api_value" in display_lvgl
    assert "proflame2_ui_body" in display_lvgl
    assert "proflame2_ui_left_panel" in display_lvgl
    assert "proflame2_ui_right_panel" in display_lvgl


def test_bare_gdo0_edge_capture_is_not_exposed_through_ha_yaml() -> None:
    yaml = _read("packages/proflame2_tembed_base.yaml")
    header = _read("components/proflame2_tembed/proflame2_tembed.h")
    component = _read("components/proflame2_tembed/proflame2_tembed.cpp")

    assert "dn022_bare_gdo0_edge_capture" not in yaml
    assert "RX Capture Profile" not in yaml
    assert "RX Always Listen Debug" not in yaml
    assert "RX Level Assignment" not in yaml
    for removed_symbol in (
        "RXEdgeCaptureStore",
        "RXSymbolCaptureStore",
        "rx_gdo_edge_isr_",
        "start_rx_listening_",
        "poll_rx_capture_",
        "dump_rx_window_capture_",
        "maybe_log_rx_summary_",
        "rx_capture_profile_",
        "rx_demod_profile_",
        "rx_qualification_mode_",
    ):
        assert removed_symbol not in header
        assert removed_symbol not in component


def test_display_boot_state_has_visible_default_label() -> None:
    state = _read("components/proflame2_tembed/display_state.h")
    component = _read("components/proflame2_tembed/proflame2_tembed.cpp")
    battery = _read("components/proflame2_tembed/battery_monitor.cpp")
    display_controller = _read("components/proflame2_tembed/display_controller.cpp")

    assert 'std::string fireplace_state_label{"PF2 READY"};' in state
    assert "int fireplace_front{-1};" in state
    assert "int fireplace_aux{-1};" in state
    assert 'this->display_.fireplace_state_label = "READY";' in component
    assert "Initial UI refresh requested" in component
    assert "Display refresh pending set" in component
    assert 'return "PROD";' in display_controller
    assert 'return "READY";' in component
    assert 'return "TX";' in component
    assert 'return "ERR";' in component
    assert "enum class DisplayRightPanelPage" in state
    assert "DisplayRightPanelPage right_panel_page{DisplayRightPanelPage::ACTIVITY};" in state
    assert "bool display_dimmed{false};" in state
    assert "void set_display_dim_timeout_min(uint32_t value)" in _read("components/proflame2_tembed/proflame2_tembed.h")
    assert "void set_display_wake_on_activity(bool value)" in _read("components/proflame2_tembed/proflame2_tembed.h")
    assert "void set_display_dim_level(uint8_t value);" in _read("components/proflame2_tembed/proflame2_tembed.h")
    assert "bool rx_active_listener_requested_{false};" in _read("components/proflame2_tembed/proflame2_tembed.h")
    assert "float get_display_backlight_level() const;" in _read("components/proflame2_tembed/proflame2_tembed.h")
    assert "void handle_center_button_press();" in _read("components/proflame2_tembed/proflame2_tembed.h")
    assert "const bool was_dimmed = this->display_.display_dimmed;" in component
    assert "cycle_right_panel_page_();" in component
    assert "this->display_dim_timeout_ms_ > 0U" in component
    assert "this->rx_always_listen_debug_" not in component
    assert "this->display_dim_deferred_ = true;" in component
    assert "mark_display_activity_(this->display_wake_on_activity_);" in component
    assert "return static_cast<float>(this->display_dim_level_) / 10.0f;" in component
    assert "!this->rx_active_listener_requested_" in component
    assert "if (changed) {" in component
    assert "pending_display_intent_.valid" in component
    assert "apply_pending_display_intent_" in component
    assert "BQ27220_REG_SOC = 0x2C" in battery
    assert "BQ27220_REG_VOLTAGE = 0x08" in battery
    assert "BQ25896_REG_STATUS = 0x0B" in battery
    assert "poll_battery_status_()" in component
    assert 'CONF_BATTERY_PERCENT = "battery_percent"' in _read("components/proflame2_tembed/__init__.py")
    assert "set_battery_percent_sensor" in _read("components/proflame2_tembed/proflame2_tembed.h")
    assert 'device_class="battery"' in _read("components/proflame2_tembed/__init__.py")
    assert 'entity_category="diagnostic"' in _read("components/proflame2_tembed/__init__.py")
    assert "publish_state(NAN)" in component
    assert 'value += "U"' not in component
    assert 'value += "+"' not in component
    assert "poll_network_status_()" not in component
    assert "get_display_wifi_text() const" in component
    assert "get_display_api_text() const" in component
    assert "DisplayController::connection_text" in display_controller
    assert "DisplayController::left_details_text" in display_controller


def test_esphome_base_package_does_not_require_display_package() -> None:
    base = _read("packages/proflame2_tembed_base.yaml")
    example = _read("examples/lilygo_cc1101_example.yaml")

    assert "packages:" not in base or "proflame2_tembed_display" not in base
    assert "proflame2_tembed_display: !include ../packages/proflame2_tembed_display.yaml" in example


def test_esphome_yaml_documents_tembed_board_facts() -> None:
    combined = "\n".join(
        [
            _read("README.md"),
            _read("examples/lilygo_cc1101_example.yaml"),
            _read("packages/proflame2_tembed_base.yaml"),
            _read("components/proflame2_tembed/radio_cc1101.h"),
        ]
    )

    for expected in (
        "GPIO15",
        "GPIO11",
        "GPIO9",
        "GPIO10",
        "GPIO21",
        "GPIO41",
        "GPIO16",
        "GPIO40",
        "GPIO12",
        "GPIO3",
        "GPIO38",
        "GPIO47",
        "GPIO48",
        "GPIO8",
        "GPIO18",
        "SW1: GPIO47 = HIGH",
        "SW0: GPIO48 = LOW",
    ):
        assert expected in combined


def test_esphome_cpp_tx_path_stays_transport_only() -> None:
    source = _read("components/proflame2_tembed/proflame2_tembed.cpp")
    header = _read("components/proflame2_tembed/proflame2_tembed.h")
    radio = _read("components/proflame2_tembed/radio_cc1101.cpp")
    radio_tx = _read("components/proflame2_tembed/radio_cc1101_tx.cpp")
    tx_controller = _read("components/proflame2_tembed/tx_controller.cpp")
    combined = f"{source}\n{header}\n{radio}\n{radio_tx}\n{tx_controller}"

    assert "cc1101_async_gdo0_msb_first" in combined
    assert "class TxController" in _read("components/proflame2_tembed/tx_controller.h")
    assert "TxController::validate_payload_request" in tx_controller
    assert "TxController::is_hex_payload_" in combined
    assert "repeat_count_mismatch" in combined
    assert "tx_success_count_" in combined
    assert "tx_failure_count_" in combined
    assert "enqueue_tx_" in combined
    assert "display_state_update(" in combined
    assert "process_pending_operation_" in combined
    assert "inter_frame_gap_us_" in combined
    assert "continuous_burst" in combined
    assert "repeated_strobe" in combined
    assert "pre_burst_low_us_" in combined
    assert "pre_frame_low_us_" in combined
    assert "diagnostic_repeat_count_override_" in combined
    assert "TX first_bits[" in combined
    assert "TX bit[" in combined
    assert "transmit_async_ook" in combined
    assert "transmit_test_pattern_async_ook" in combined
    assert "wait_until_" in combined
    assert "partnum" in combined.lower()
    assert "marcstate" in combined.lower()

    forbidden_protocol_authority_terms = (
        "err1_for",
        "err2_for",
        "encode_packet",
        "decode_packet",
        "FireplaceState",
        "RemoteProfile",
        "thermostat_policy",
    )
    for forbidden in forbidden_protocol_authority_terms:
        assert forbidden not in combined


def test_esphome_native_group_serializer_supports_variable_emit_lengths() -> None:
    radio = _read("components/proflame2_tembed/radio_cc1101_tx.cpp")

    assert "PROFLAME_NATIVE_SOURCE_BITS_PER_GROUP = 9" in radio
    assert "PROFLAME_NATIVE_MAX_EMIT_BITS_PER_GROUP = 16" in radio
    assert "group3_derive_failed" not in radio
    assert "unexpected_high_run:" in radio
    assert "emit_overflow" in radio
    assert "invalid_native_group_payload:" in radio
    assert "derive_native_group_emit_bits_" in radio
    assert "native_group_air_bits_" in radio
    assert "native_group_run_lengths_" in radio


def test_esphome_rf_envelope_defaults_keep_active_pa_and_native_repeat_gap() -> None:
    component = _read("components/proflame2_tembed/__init__.py")
    header = _read("components/proflame2_tembed/radio_cc1101.h")
    radio = _read("components/proflame2_tembed/radio_cc1101.cpp")
    radio_rx = _read("components/proflame2_tembed/radio_cc1101_rx.cpp")
    radio_tx = _read("components/proflame2_tembed/radio_cc1101_tx.cpp")
    radio_combined = f"{radio}\n{radio_rx}\n{radio_tx}"

    assert 'CONF_NATIVE_GROUP_TIMING_PROFILE = "native_group_timing_profile"' in component
    assert 'CONF_NATIVE_GROUP_REPEAT_BOUNDARY_MODE = "native_group_repeat_boundary_mode"' in component
    assert '"yardstick_compat", "native_remote"' in component
    assert '"continuous_tx", "reenter_tx"' in component
    assert "NativeGroupTimingProfile" in component
    assert "NativeGroupRepeatBoundaryMode" in component
    assert "enum class NativeGroupTimingProfile" in header
    assert "enum class NativeGroupRepeatBoundaryMode" in header
    assert "YARDSTICK_COMPAT = 0" in header
    assert "NATIVE_REMOTE = 1" in header
    assert "CONTINUOUS_TX = 0" in header
    assert "REENTER_TX = 1" in header
    assert "PROFLAME_NATIVE_YARDSTICK_DEFAULT_REPEAT_GAP_US = 10700" in radio_tx
    assert "PROFLAME_NATIVE_REMOTE_DEFAULT_REPEAT_GAP_US = 5240" in radio_tx
    assert "PROFLAME_NATIVE_REMOTE_SHORT_HIGH_US = 408" in radio_tx
    assert "PROFLAME_NATIVE_REMOTE_LONG_HIGH_US = 820" in radio_tx
    assert "PROFLAME_NATIVE_REMOTE_SYNC_HIGH_US = 1224" in radio_tx
    assert "PROFLAME_NATIVE_REMOTE_SHORT_LOW_US = 424" in radio_tx
    assert "PROFLAME_NATIVE_REMOTE_LONG_LOW_US = 832" in radio_tx
    assert "PROFLAME_NATIVE_REMOTE_SHORT_LOW_SCHEDULE_BIAS_US = 20" in radio_tx
    assert "PROFLAME_NATIVE_REMOTE_LONG_LOW_SCHEDULE_BIAS_US = 20" in radio_tx
    assert "spec.desired_sync = PWMSymbolTiming{PROFLAME_NATIVE_REMOTE_SYNC_HIGH_US," in radio_tx
    assert "PROFLAME_NATIVE_REMOTE_LONG_LOW_US};" in radio_tx
    assert "spec.desired_one = PWMSymbolTiming{PROFLAME_NATIVE_REMOTE_LONG_HIGH_US," in radio_tx
    assert "PROFLAME_NATIVE_REMOTE_SHORT_LOW_US};" in radio_tx
    assert "PROFLAME_NATIVE_REMOTE_SHORT_HIGH_COMPENSATION_US = 12" in radio_tx
    assert "PROFLAME_NATIVE_REMOTE_LONG_HIGH_COMPENSATION_US = 12" in radio_tx
    assert "PROFLAME_NATIVE_REMOTE_SYNC_HIGH_COMPENSATION_US = 8" in radio_tx
    assert "PROFLAME_NATIVE_REMOTE_REPEAT_GAP_COMPENSATION_US = 0" in radio_tx
    assert "CC1101_PATABLE_OOK_OFF_VALUE = 0x00" in radio_tx
    assert "CC1101_PATABLE_OOK_ON_VALUE = 0xC6" in radio_tx
    assert "struct NativeGroupTimingProfileSpec" in radio_tx
    assert "static NativeGroupTimingProfileSpec native_group_timing_profile_spec_" in radio_tx
    assert "static PWMSymbolTiming native_group_symbol_timing_from_spec_" in radio_tx
    assert "static uint32_t native_remote_pcm_expected_bits_" in radio_tx
    assert "static bool choose_native_remote_symbol_timing_from_pcm_row_" in radio_tx
    assert "compensated_duration_us_" in radio_tx
    assert "if (profile == NativeGroupTimingProfile::NATIVE_REMOTE)" in radio_tx
    assert "scheduled_zero = PWMSymbolTiming{" in radio_tx
    assert "scheduled_one = PWMSymbolTiming{" in radio_tx
    assert "scheduled_sync = PWMSymbolTiming{" in radio_tx
    assert "spec.desired_sync.low_us + PROFLAME_NATIVE_REMOTE_LONG_LOW_SCHEDULE_BIAS_US" in radio_tx
    assert "spec.desired_one.low_us + PROFLAME_NATIVE_REMOTE_SHORT_LOW_SCHEDULE_BIAS_US" in radio_tx
    assert "Candidate{PWMSymbolTiming{spec.scheduled_zero.high_us, spec.scheduled_sync.low_us}, 2U}" in radio_tx
    assert "Candidate{PWMSymbolTiming{spec.scheduled_one.high_us, spec.scheduled_sync.low_us}, 2U}" in radio_tx
    assert "spec.desired_zero = PWMSymbolTiming{bit_period_us, bit_period_us};" in radio_tx
    assert "spec.desired_one = PWMSymbolTiming{bit_period_us * 2U, bit_period_us * 2U};" in radio_tx
    assert "desired_repeat_gap_us=%" not in radio_combined
    assert "const uint32_t desired_native_repeat_gap_us =" in radio_tx
    assert "const uint32_t scheduled_native_repeat_gap_us =" in radio_tx
    assert "native_group_repeat_boundary_mode == NativeGroupRepeatBoundaryMode::REENTER_TX" in radio_tx
    assert "timing.inter_repeat_gap_measured_us =" in radio_tx
    assert "timing.min_repeat_duration_us = std::min" in radio_tx
    assert "uint32_t setup_before_gap_us = 0;" in radio_tx
    assert "uint32_t setup_inside_gap_us = 0;" in radio_tx
    assert "target_first_rising_edge_us =" in radio_tx
    assert "if (transmission_index == 0 && pre_burst_low_us > 0)" in radio_tx
    assert "if (pre_frame_low_us > 0)" in radio_tx
    assert "expected_native_remote_pcm_bits =" in radio_tx
    assert "native_remote_pcm_cursor" in radio_tx
    assert "this->async_tx_pin_()->digital_write(true);" in radio_tx
    assert "if (!reenter_tx_between_repeats)" in radio_tx
    assert "if (reenter_tx_between_repeats) {" in radio_tx
    assert "NativeGroupRepeatBoundaryMode::REENTER_TX" in radio_tx
    assert "NativeGroupRepeatBoundaryMode::CONTINUOUS_TX" in radio_combined
    assert "timing.inter_repeat_gap_min_us =" in radio_tx
    assert "timing.inter_repeat_gap_max_us =" in radio_tx
    assert "timing.inter_repeat_gap_total_us +=" in radio_tx
    assert "timing.first_rising_edge_late_min_us =" in radio_tx
    assert "timing.first_rising_edge_late_max_us =" in radio_tx
    assert "timing.first_rising_edge_late_total_us +=" in radio_tx
    assert "effective_pa_entry0=0x%02X" in radio
    assert "const uint8_t pa_table[8] = {" in radio_tx
    assert "CC1101_PATABLE_OOK_OFF_VALUE, CC1101_PATABLE_OOK_ON_VALUE" in radio_tx
    assert "CC1101 async OOK PA levels logic0=0x%02X logic1=0x%02X FREND0.PA_POWER=%u" in radio_tx


def test_esphome_cc1101_debug_trace_is_compile_time_gated() -> None:
    header = _read("components/proflame2_tembed/radio_cc1101.h")
    radio = _read("components/proflame2_tembed/radio_cc1101.cpp")
    radio_rx = _read("components/proflame2_tembed/radio_cc1101_rx.cpp")
    radio_tx = _read("components/proflame2_tembed/radio_cc1101_tx.cpp")
    component = _read("components/proflame2_tembed/proflame2_tembed.cpp")

    assert "#define PROFLAME2_TEMBED_TX_DEBUG 1" in header
    assert "#if PROFLAME2_TEMBED_TX_DEBUG" in header
    assert "bool RadioCC1101::rx_fifo_probe" in radio_rx
    assert "#if PROFLAME2_TEMBED_TX_DEBUG" in radio_tx
    assert "TX detailed diagnostics %s (PROFLAME2_TEMBED_TX_DEBUG=%u)" in radio_tx
    assert "TX_DEBUG_COMPILE_STATE" in radio_tx
    assert "transmit_async_ook" not in radio
    assert "rx_fifo_probe" not in radio
    assert "drain_debug_tx_diagnostics" in radio_tx
    assert "capture_bit_timing_sample_" in radio_tx
    assert "capture_first_bits_" in radio_tx
    assert "TX timing-critical region begins here" in radio_tx
    assert "TX timing-critical region ends here" in radio_tx
    assert "store_deferred_debug_trace_" in component
    assert "drain_deferred_debug_trace_" in component
    assert "this->store_deferred_debug_trace_(result.timing, result.tx_mode);" in component
    assert "this->publish_telemetry_();" in component


def test_esphome_readme_documents_source_only_distribution_policy() -> None:
    readme = _read("README.md")
    normalized = " ".join(readme.split())

    assert "source and configuration only" in normalized
    assert "does not distribute prebuilt firmware binaries" in normalized
    assert "ESPHome Builder" in normalized
    assert "pin the GitHub `ref` to a release tag" in normalized
    assert "compiled firmware outputs must be discarded" in normalized
    assert "R820T/`rtl_433` witness" in normalized
