"""Runtime/config-entry tests for mocked ESPHome backend selection."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

homeassistant = pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

pytestmark = pytest.mark.ha

from homeassistant.const import ATTR_DEVICE_ID, EVENT_HOMEASSISTANT_STOP
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proflame2.const import (
    BACKEND_ESPHOME,
    BACKEND_YARDSTICK,
    CONF_ACTIVE_LISTENING,
    CONF_BACKEND_TYPE,
    CONF_C1,
    CONF_C2,
    CONF_D1,
    CONF_D2,
    CONF_ESPHOME_ENTRY_ID,
    CONF_FLAME,
    CONF_POWER,
    CONF_REMOTE_ID,
    DATA_CONFIRMATION_WINDOW_SECONDS,
    DATA_ESPHOME_TRANSPORT_FACTORY,
    DOMAIN,
    available_backend_types,
)
from custom_components.proflame2.diagnostics import async_get_config_entry_diagnostics
from custom_components.proflame2.rf.esphome.contract import ESPHomeEndpointStatus
from custom_components.proflame2.rf.esphome.transport import (
    ESPHOME_RX_EVENT_TYPE,
    HomeAssistantESPHomeTransport,
    MockESPHomeTransport,
)
from custom_components.proflame2.rf.esphome_api import ESPHomeAPIBackend
from custom_components.proflame2.rf.yardstick import YardStickBackend
from custom_components.proflame2.runtime import (
    async_get_runtime_entries,
    async_refresh_runtime_device_link,
    async_retry_runtime_device_link,
    async_setup_runtime_entry,
)
from custom_components.proflame2.services import async_start_active_listener


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading integrations from the local custom_components directory."""

    yield


def _add_entry(
    hass,
    *,
    backend_type: str = BACKEND_ESPHOME,
    title: str = "Living Room",
    remote_id: int = 0x3B3F02,
    options: dict | None = None,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={
            "name": title,
            CONF_BACKEND_TYPE: backend_type,
            CONF_REMOTE_ID: remote_id,
            CONF_C1: 5,
            CONF_D1: 7,
            CONF_C2: 1,
            CONF_D2: 8,
        },
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


def _install_mock_esphome_transport(hass, transport: MockESPHomeTransport) -> None:
    hass.data.setdefault(DOMAIN, {})[DATA_ESPHOME_TRANSPORT_FACTORY] = lambda _hass, _entry: transport


class _FakeESPHomeService:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeESPHomeInfo:
    def __init__(self, object_id: str, key: int) -> None:
        self.object_id = object_id
        self.key = key
        self.name = object_id


class _FakeESPHomeState:
    def __init__(self, key: int, state) -> None:
        self.key = key
        self.state = state


class _FakeESPHomeClient:
    def __init__(self, runtime_data, *, execute_error: str | None = None) -> None:
        self.runtime_data = runtime_data
        self.execute_error = execute_error
        self.calls: list[tuple[str, dict]] = []

    async def execute_service(self, service, data) -> None:
        self.calls.append((service.name, dict(data)))
        if self.execute_error is not None:
            raise RuntimeError(self.execute_error)

        if service.name == "proflame2_display_state_update":
            return
        if service.name == "proflame2_learn_mode_update":
            self.runtime_data.learning_mode_updates.append(dict(data))
            return
        if service.name == "proflame2_rx_set_active_listening":
            self.runtime_data.active_listening_enabled = bool(data.get("enabled"))
            self.runtime_data.active_listening_profile = {
                "serial_id": data.get("serial_id"),
                "c1": data.get("c1"),
                "d1": data.get("d1"),
                "c2": data.get("c2"),
                "d2": data.get("d2"),
            }
            return
        if service.name == "proflame2_rx_stop":
            self.runtime_data.rx_requested = False
            self.runtime_data.active_listening_enabled = False
            return
        if service.name == "proflame2_rx_end_confirmation":
            self.runtime_data.rx_requested = False
            return

        request_id = data["request_id"]
        payload_hex = data["air_payload_hex"]
        payload_bit_length = int(data["payload_bit_length"])
        repeat_count = int(data["repeat_count"])
        expected_repeat_count = int(self.runtime_data.object_states["proflame2_tx_repeat_count"].state)
        reported_repeat_count = getattr(self.runtime_data, "reported_repeat_count_override", None)

        self.runtime_data.object_states["proflame2_last_request_id"].state = request_id
        self.runtime_data.object_states["proflame2_last_request_repeat_count"].state = (
            reported_repeat_count if reported_repeat_count is not None else repeat_count
        )
        self.runtime_data.object_states["proflame2_last_tx_path"].state = "cc1101_async_gdo0_msb_first"
        self.runtime_data.object_states["proflame2_last_payload_hex"].state = payload_hex
        self.runtime_data.object_states["proflame2_last_marcstate_before_tx"].state = "0x13"
        self.runtime_data.object_states["proflame2_last_marcstate_after_tx"].state = "0x01"
        self.runtime_data.object_states["proflame2_cc1101_partnum"].state = "0x00"
        self.runtime_data.object_states["proflame2_cc1101_version"].state = "0x14"
        self.runtime_data.object_states["proflame2_last_tx_elapsed_ms"].state = 417

        if len(payload_hex) % 2 != 0 or any(character not in "0123456789abcdefABCDEF" for character in payload_hex):
            self.runtime_data.object_states["proflame2_last_error"].state = "invalid_hex_payload"
            self.runtime_data.object_states["proflame2_last_tx_result"].state = "error:invalid_hex_payload"
            self.runtime_data.object_states["proflame2_tx_failure_count"].state += 1
            self.runtime_data.object_states["proflame2_last_payload_length"].state = 0
            self.runtime_data.object_states["proflame2_endpoint_status"].state = "fault"
            return

        if payload_bit_length <= 0 or payload_bit_length > ((len(payload_hex) // 2) * 8):
            self.runtime_data.object_states["proflame2_last_error"].state = "invalid_payload_bit_length"
            self.runtime_data.object_states["proflame2_last_tx_result"].state = "error:invalid_payload_bit_length"
            self.runtime_data.object_states["proflame2_tx_failure_count"].state += 1
            self.runtime_data.object_states["proflame2_last_payload_length"].state = 0
            self.runtime_data.object_states["proflame2_endpoint_status"].state = "fault"
            return

        if repeat_count != expected_repeat_count:
            self.runtime_data.object_states["proflame2_last_error"].state = "repeat_count_mismatch"
            self.runtime_data.object_states["proflame2_last_tx_result"].state = "error:repeat_count_mismatch"
            self.runtime_data.object_states["proflame2_tx_failure_count"].state += 1
            self.runtime_data.object_states["proflame2_last_payload_length"].state = 0
            self.runtime_data.object_states["proflame2_endpoint_status"].state = "fault"
            return

        self.runtime_data.object_states["proflame2_last_error"].state = ""
        self.runtime_data.object_states["proflame2_last_tx_result"].state = "ok"
        self.runtime_data.object_states["proflame2_tx_success_count"].state += 1
        self.runtime_data.object_states["proflame2_last_payload_length"].state = len(payload_hex) // 2
        self.runtime_data.object_states["proflame2_endpoint_status"].state = "ready/rx_supported"


def _fake_esphome_runtime_data(
    *,
    repeat_count: int = 5,
    reported_repeat_count_override: int | None = None,
    execute_error: str | None = None,
):
    from collections import defaultdict
    from types import SimpleNamespace

    object_states = {
        "proflame2_endpoint_status": _FakeESPHomeState(1, "ready/rx_supported"),
        "proflame2_last_error": _FakeESPHomeState(2, ""),
        "proflame2_last_tx_result": _FakeESPHomeState(3, "none"),
        "proflame2_last_request_id": _FakeESPHomeState(4, ""),
        "proflame2_tx_success_count": _FakeESPHomeState(5, 0),
        "proflame2_tx_failure_count": _FakeESPHomeState(6, 0),
        "proflame2_last_payload_length": _FakeESPHomeState(7, 0),
        "proflame2_tx_repeat_count": _FakeESPHomeState(8, repeat_count),
        "proflame2_last_request_repeat_count": _FakeESPHomeState(9, 0),
        "proflame2_firmware_protocol_version": _FakeESPHomeState(10, 1),
        "proflame2_config_revision": _FakeESPHomeState(11, 1),
        "proflame2_last_tx_path": _FakeESPHomeState(12, "none"),
        "proflame2_last_tx_elapsed_ms": _FakeESPHomeState(13, 0),
        "proflame2_last_payload_hex": _FakeESPHomeState(14, ""),
        "proflame2_last_marcstate_before_tx": _FakeESPHomeState(15, "unknown"),
        "proflame2_last_marcstate_after_tx": _FakeESPHomeState(16, "unknown"),
        "proflame2_cc1101_partnum": _FakeESPHomeState(17, "unknown"),
        "proflame2_cc1101_version": _FakeESPHomeState(18, "unknown"),
        "proflame2_rx_dropped_packets": _FakeESPHomeState(19, 0),
        "proflame2_rx_no_rf_captures": _FakeESPHomeState(20, 1),
        "proflame2_rx_incomplete_fifo_captures": _FakeESPHomeState(21, 2),
        "proflame2_rx_decode_failures": _FakeESPHomeState(22, 3),
        "proflame2_rx_profile_mismatches": _FakeESPHomeState(23, 4),
        "proflame2_rx_accepted_packets": _FakeESPHomeState(24, 5),
        "proflame2_rx_tx_suppressed": _FakeESPHomeState(25, 6),
        "proflame2_rx_transport_unavailable": _FakeESPHomeState(26, 7),
        "proflame2_rx_last_rejection_snapshot": _FakeESPHomeState(27, "stage=decode_failed"),
    }
    info_map = {state.key: _FakeESPHomeInfo(object_id, state.key) for object_id, state in object_states.items()}
    state_map = {state.key: state for state in object_states.values()}

    runtime_data = SimpleNamespace(
        services={
            1: _FakeESPHomeService("proflame2_tx_stateful"),
            2: _FakeESPHomeService("proflame2_display_state_update"),
            3: _FakeESPHomeService("proflame2_rx_set_active_listening"),
            4: _FakeESPHomeService("proflame2_rx_stop"),
            5: _FakeESPHomeService("proflame2_rx_end_confirmation"),
            6: _FakeESPHomeService("proflame2_learn_mode_update"),
        },
        available=True,
        info={_FakeESPHomeInfo: info_map},
        state=defaultdict(dict, {_FakeESPHomeState: state_map}),
        device_info=SimpleNamespace(esphome_version="2025.5.2"),
        object_states=object_states,
        reported_repeat_count_override=reported_repeat_count_override,
        rx_requested=False,
        active_listening_enabled=False,
        active_listening_profile=None,
        learning_mode_updates=[],
    )
    runtime_data.client = _FakeESPHomeClient(runtime_data, execute_error=execute_error)
    return runtime_data


def _add_linked_esphome_device(
    hass,
    entry: MockConfigEntry,
    *,
    identifier: tuple[str, str] | None = None,
    connection: tuple[str, str] | None = None,
) -> str:
    registry = dr.async_get(hass)
    create_kwargs = dict(
        config_entry_id=entry.entry_id,
        manufacturer="ESPHome",
        name=entry.title,
        model="T-Embed",
    )
    if identifier is not None:
        create_kwargs["identifiers"] = {identifier}
    if connection is not None:
        create_kwargs["connections"] = {connection}
    device = registry.async_get_or_create(**create_kwargs)
    return device.id


def _add_linked_esphome_entry(hass, runtime_data) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain="esphome",
        title="T-Embed",
        data={"host": "192.0.2.10", "port": 6053},
    )
    entry.runtime_data = runtime_data
    entry.add_to_hass(hass)
    return entry


async def test_runtime_can_create_esphome_backend_with_mock_transport(hass) -> None:
    transport = MockESPHomeTransport()
    _install_mock_esphome_transport(hass, transport)
    transport = MockESPHomeTransport()
    _install_mock_esphome_transport(hass, transport)
    transport = MockESPHomeTransport()
    _install_mock_esphome_transport(hass, transport)
    entry = _add_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    assert runtime_entry.backend_type == BACKEND_ESPHOME
    assert isinstance(runtime_entry.backend, ESPHomeAPIBackend)
    assert runtime_entry.backend.transport is transport
    assert transport.connected is True
    assert transport.configured is True


async def test_runtime_rejects_esphome_without_transport_factory(hass) -> None:
    entry = _add_entry(hass)

    with pytest.raises(RuntimeError, match="no linked ESPHome entry id is present"):
        await async_setup_runtime_entry(hass, entry)


async def test_runtime_accepts_async_esphome_transport_factory(hass) -> None:
    transport = MockESPHomeTransport()

    async def factory(_hass, _entry):
        return transport

    hass.data.setdefault(DOMAIN, {})[DATA_ESPHOME_TRANSPORT_FACTORY] = factory
    entry = _add_entry(hass)

    runtime_entry = await async_setup_runtime_entry(hass, entry)

    assert isinstance(runtime_entry.backend, ESPHomeAPIBackend)
    assert runtime_entry.backend.transport is transport


async def test_yardstick_backend_path_is_unchanged(hass, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_connect(self):
        raise AssertionError("Yard Stick should not be connected during runtime setup")

    monkeypatch.setattr(YardStickBackend, "connect", fake_connect)
    entry = _add_entry(hass, backend_type=BACKEND_YARDSTICK)

    assert await hass.config_entries.async_setup(entry.entry_id)

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    assert runtime_entry.backend_type == BACKEND_YARDSTICK
    assert isinstance(runtime_entry.backend, YardStickBackend)


async def test_runtime_can_create_real_esphome_transport_from_linked_entry(hass) -> None:
    linked_entry = _add_linked_esphome_entry(hass, _fake_esphome_runtime_data())
    entry = _add_entry(
        hass,
        options={CONF_ESPHOME_ENTRY_ID: linked_entry.entry_id},
    )

    assert await hass.config_entries.async_setup(entry.entry_id)

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    assert isinstance(runtime_entry.backend, ESPHomeAPIBackend)
    assert isinstance(runtime_entry.backend.transport, HomeAssistantESPHomeTransport)
    assert runtime_entry.backend.transport.linked_entry_id == linked_entry.entry_id


async def test_esphome_config_entry_unload_and_reload_closes_transport(hass) -> None:
    first_transport = MockESPHomeTransport()
    transports = [first_transport]

    def factory(_hass, _entry):
        transport = transports[-1]
        return transport

    hass.data.setdefault(DOMAIN, {})[DATA_ESPHOME_TRANSPORT_FACTORY] = factory
    entry = _add_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    backend = async_get_runtime_entries(hass)[entry.entry_id].backend
    assert isinstance(backend, ESPHomeAPIBackend)
    await backend.connect()

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert first_transport.close_count == 1
    assert entry.entry_id not in async_get_runtime_entries(hass)

    second_transport = MockESPHomeTransport()
    transports.append(second_transport)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert async_get_runtime_entries(hass)[entry.entry_id].backend is not backend


async def test_esphome_shutdown_closes_backend_and_marks_runtime(hass) -> None:
    transport = MockESPHomeTransport()
    _install_mock_esphome_transport(hass, transport)
    entry = _add_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    backend = async_get_runtime_entries(hass)[entry.entry_id].backend
    assert isinstance(backend, ESPHomeAPIBackend)
    await backend.connect()

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    assert runtime_entry.shutting_down is True
    assert runtime_entry.shutdown_reason == "ha_shutdown"
    assert transport.close_count == 1


async def test_esphome_diagnostics_include_backend_and_transport_status(hass) -> None:
    transport = MockESPHomeTransport()
    _install_mock_esphome_transport(hass, transport)
    entry = _add_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    backend = async_get_runtime_entries(hass)[entry.entry_id].backend
    assert isinstance(backend, ESPHomeAPIBackend)
    await backend.connect()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    backend_diagnostics = diagnostics["runtime"]["backend_diagnostics"]

    assert backend_diagnostics["backend_name"] == BACKEND_ESPHOME
    assert backend_diagnostics["controller_id"] == BACKEND_ESPHOME
    assert backend_diagnostics["connected"] is True
    assert backend_diagnostics["radio_config"]["tx_repeat_count"] == 5
    assert backend_diagnostics["transport"]["class"] == "MockESPHomeTransport"
    assert backend_diagnostics["endpoint_status"]["status"] == "ready"


async def test_service_path_reaches_esphome_mock_transport(hass) -> None:
    transport = MockESPHomeTransport()
    _install_mock_esphome_transport(hass, transport)
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0
    entry = _add_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)

    await hass.services.async_call(
        DOMAIN,
        "set_state",
        {
            CONF_POWER: True,
            CONF_FLAME: 2,
        },
        blocking=True,
    )

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    assert runtime_entry.last_packet is not None
    assert runtime_entry.last_packet.transmission_plan is not None
    assert transport.tx_requests[-1].air_payload == (runtime_entry.last_packet.transmission_plan.air_payload)
    assert runtime_entry.last_send_result is not None
    assert runtime_entry.last_send_result.backend_name == BACKEND_ESPHOME


async def test_service_path_reaches_linked_esphome_native_action(hass) -> None:
    linked_runtime_data = _fake_esphome_runtime_data()
    linked_entry = _add_linked_esphome_entry(hass, linked_runtime_data)
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0
    entry = _add_entry(
        hass,
        options={CONF_ESPHOME_ENTRY_ID: linked_entry.entry_id},
    )

    assert await hass.config_entries.async_setup(entry.entry_id)

    await hass.services.async_call(
        DOMAIN,
        "set_state",
        {
            CONF_POWER: True,
            CONF_FLAME: 2,
        },
        blocking=True,
    )

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    transport = runtime_entry.backend.transport
    assert isinstance(transport, HomeAssistantESPHomeTransport)
    assert linked_runtime_data.client.calls[-1][0] == "proflame2_tx_stateful"
    assert (
        linked_runtime_data.client.calls[-1][1]["air_payload_hex"]
        == runtime_entry.last_packet.transmission_plan.air_payload.hex()
    )
    assert (
        linked_runtime_data.client.calls[-1][1]["payload_bit_length"]
        == runtime_entry.last_packet.transmission_plan.air_payload_bit_length
    )
    assert linked_runtime_data.client.calls[-1][1]["repeat_count"] == 5
    assert runtime_entry.last_send_result is not None
    assert runtime_entry.last_send_result.backend_name == BACKEND_ESPHOME


async def test_service_path_tolerates_linked_esphome_diagnostic_repeat_override(
    hass,
) -> None:
    linked_runtime_data = _fake_esphome_runtime_data(reported_repeat_count_override=1)
    linked_entry = _add_linked_esphome_entry(hass, linked_runtime_data)
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0
    entry = _add_entry(
        hass,
        options={CONF_ESPHOME_ENTRY_ID: linked_entry.entry_id},
    )

    assert await hass.config_entries.async_setup(entry.entry_id)

    await hass.services.async_call(
        DOMAIN,
        "set_state",
        {
            CONF_POWER: True,
            CONF_FLAME: 2,
        },
        blocking=True,
    )

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    transport = runtime_entry.backend.transport
    assert isinstance(transport, HomeAssistantESPHomeTransport)
    assert linked_runtime_data.client.calls[-1][1]["repeat_count"] == 5
    assert runtime_entry.last_send_result is not None
    assert runtime_entry.last_send_result.backend_name == BACKEND_ESPHOME
    assert runtime_entry.last_send_result.packet is runtime_entry.last_packet


async def test_transport_receives_rx_event_from_linked_esphome_device_id(hass) -> None:
    linked_runtime_data = _fake_esphome_runtime_data()
    linked_entry = _add_linked_esphome_entry(hass, linked_runtime_data)
    controller_device_id = _add_linked_esphome_device(hass, linked_entry, connection=("mac", "98:a3:16:e0:f2:e4"))
    transport = HomeAssistantESPHomeTransport(
        hass,
        linked_entry_id=linked_entry.entry_id,
        controller_id=f"esphome:{linked_entry.entry_id}",
        debug_logging_enabled=False,
    )

    await transport.connect()
    await transport.configure_radio(ESPHomeAPIBackend().radio_config)
    assert transport.can_receive is True

    hass.bus.async_fire(
        ESPHOME_RX_EVENT_TYPE,
        {
            ATTR_DEVICE_ID: controller_device_id,
            "schema_version": "1",
            "protocol": "proflame2",
            "event_kind": "rx_packet",
            "payload_hex": "00112233445566778899aabbccddeeff001122334455667788",
            "bit_count": "200",
            "packet_count": "7",
            "rssi": "-42.5",
            "lqi": "87",
            "device_tick_ms": "12345",
            "freq_hz": "314973000",
            "qualifier": "basic",
        },
    )
    await hass.async_block_till_done()

    event = await transport.receive_rx_event(timeout=0.1)
    assert event is not None
    assert event.raw_payload.hex() == "00112233445566778899aabbccddeeff001122334455667788"
    assert event.device_tick_ms == 12345
    assert event.rssi == -42.5
    assert event.lqi == 87
    assert event.frequency_hz == 314973000
    assert event.capture_metadata["schema_version"] == "1"


async def test_transport_receives_fifo_capture_event_from_linked_esphome_device_id(hass) -> None:
    linked_runtime_data = _fake_esphome_runtime_data()
    linked_entry = _add_linked_esphome_entry(hass, linked_runtime_data)
    controller_device_id = _add_linked_esphome_device(hass, linked_entry, connection=("mac", "98:a3:16:e0:f2:e4"))
    transport = HomeAssistantESPHomeTransport(
        hass,
        linked_entry_id=linked_entry.entry_id,
        controller_id=f"esphome:{linked_entry.entry_id}",
        debug_logging_enabled=False,
    )

    await transport.connect()
    await transport.configure_radio(ESPHomeAPIBackend().radio_config)
    hass.bus.async_fire(
        ESPHOME_RX_EVENT_TYPE,
        {
            ATTR_DEVICE_ID: controller_device_id,
            "schema_version": "2",
            "protocol": "proflame2",
            "event_kind": "fifo_capture",
            "artifact_class": "raw_fifo_window",
            "source": "lilygo_cc1101_fifo",
            "payload_hex": "aabbccdd",
            "packet_count": "8",
            "rssi": "128",
            "lqi": "74",
            "device_tick_ms": "54321",
            "freq_hz": "314973000",
            "capture_mode": "rolling_fifo_trailing_window",
            "profile": "rfcat_fixed_none_rfcat_wide",
            "byte_count": "4",
            "trailing_window_complete": "YES",
            "insufficient_trailing_window": "NO",
            "rx_fifo_overflow": "NO",
        },
    )
    await hass.async_block_till_done()

    event = await transport.receive_rx_event(timeout=0.1)
    assert event is not None
    assert event.event_id.endswith(":rx:8")
    assert event.raw_payload == bytes.fromhex("aabbccdd")
    assert event.device_tick_ms == 54321
    assert event.frequency_hz == 314973000
    assert event.capture_metadata["schema_version"] == "2"
    assert event.capture_metadata["event_kind"] == "fifo_capture"
    assert event.capture_metadata["artifact_class"] == "raw_fifo_window"
    assert event.capture_metadata["capture_mode"] == "rolling_fifo_trailing_window"
    assert event.capture_metadata["trailing_window_complete"] == "YES"


async def test_transport_drains_fifo_events_after_active_listening_with_plain_ready_status(hass) -> None:
    linked_runtime_data = _fake_esphome_runtime_data()
    linked_runtime_data.object_states["proflame2_endpoint_status"].state = "ready"
    linked_entry = _add_linked_esphome_entry(hass, linked_runtime_data)
    controller_device_id = _add_linked_esphome_device(hass, linked_entry, connection=("mac", "98:a3:16:e0:f2:e4"))
    transport = HomeAssistantESPHomeTransport(
        hass,
        linked_entry_id=linked_entry.entry_id,
        controller_id=f"esphome:{linked_entry.entry_id}",
        debug_logging_enabled=False,
    )

    await transport.connect()
    await transport.configure_radio(ESPHomeAPIBackend().radio_config)
    assert transport.can_receive is True

    await transport.set_active_listening(True)
    assert transport.can_receive is True
    hass.bus.async_fire(
        ESPHOME_RX_EVENT_TYPE,
        {
            ATTR_DEVICE_ID: controller_device_id,
            "schema_version": "2",
            "protocol": "proflame2",
            "event_kind": "fifo_capture",
            "artifact_class": "raw_fifo_window",
            "source": "lilygo_cc1101_fifo",
            "payload_hex": "aabbccdd",
            "packet_count": "8",
        },
    )
    await hass.async_block_till_done()

    event = await transport.receive_rx_event(timeout=0.1)

    assert event is not None
    assert event.raw_payload == bytes.fromhex("aabbccdd")


@pytest.mark.parametrize(
    "status_text",
    [
        "ready",
        "ready/rx_supported",
        "ready/fifo_rx",
        "ready/rx_confirmation",
        "ready/rx_listening",
        "ready/tx_only",
    ],
)
async def test_transport_can_receive_tracks_rx_service_contract_not_status_text(hass, status_text: str) -> None:
    linked_runtime_data = _fake_esphome_runtime_data()
    linked_runtime_data.object_states["proflame2_endpoint_status"].state = status_text
    linked_entry = _add_linked_esphome_entry(hass, linked_runtime_data)
    _add_linked_esphome_device(hass, linked_entry, connection=("mac", "98:a3:16:e0:f2:e4"))
    transport = HomeAssistantESPHomeTransport(
        hass,
        linked_entry_id=linked_entry.entry_id,
        controller_id=f"esphome:{linked_entry.entry_id}",
        debug_logging_enabled=False,
    )

    await transport.connect()
    await transport.configure_radio(ESPHomeAPIBackend().radio_config)
    status = await transport.get_status()

    assert transport.can_receive is True
    assert status.status in {
        ESPHomeEndpointStatus.READY,
        ESPHomeEndpointStatus.RX_ACTIVE,
    }


async def test_transport_rx_policy_and_stop_invoke_linked_services(hass) -> None:
    linked_runtime_data = _fake_esphome_runtime_data()
    linked_entry = _add_linked_esphome_entry(hass, linked_runtime_data)
    _add_linked_esphome_device(hass, linked_entry, connection=("mac", "98:a3:16:e0:f2:e4"))
    transport = HomeAssistantESPHomeTransport(
        hass,
        linked_entry_id=linked_entry.entry_id,
        controller_id=f"esphome:{linked_entry.entry_id}",
        debug_logging_enabled=False,
    )

    await transport.connect()
    await transport.configure_radio(ESPHomeAPIBackend().radio_config)
    await transport.set_active_listening(True)
    await transport.stop_rx()

    assert linked_runtime_data.active_listening_enabled is False
    assert linked_runtime_data.client.calls[-2][0] == "proflame2_rx_set_active_listening"
    assert linked_runtime_data.client.calls[-1][0] == "proflame2_rx_stop"


async def test_transport_learning_mode_update_invokes_linked_service(hass) -> None:
    linked_runtime_data = _fake_esphome_runtime_data()
    linked_entry = _add_linked_esphome_entry(hass, linked_runtime_data)
    _add_linked_esphome_device(hass, linked_entry, connection=("mac", "98:a3:16:e0:f2:e4"))
    transport = HomeAssistantESPHomeTransport(
        hass,
        linked_entry_id=linked_entry.entry_id,
        controller_id=f"esphome:{linked_entry.entry_id}",
        debug_logging_enabled=False,
    )

    await transport.connect()
    await transport.configure_radio(ESPHomeAPIBackend().radio_config)
    await transport.update_learning_mode(
        active=True,
        step_title="Learn 1",
        instruction="Press Power ON",
        status="Listening",
    )

    assert linked_runtime_data.client.calls[-1] == (
        "proflame2_learn_mode_update",
        {
            "active": 1,
            "step_title": "Learn 1",
            "instruction": "Press Power ON",
            "status": "Listening",
        },
    )
    assert linked_runtime_data.learning_mode_updates[-1]["active"] == 1


async def test_transport_receive_capability_does_not_require_manual_completion_service(hass) -> None:
    linked_runtime_data = _fake_esphome_runtime_data()
    linked_runtime_data.services = {
        key: service
        for key, service in linked_runtime_data.services.items()
        if service.name != "proflame2_rx_end_confirmation"
    }
    linked_entry = _add_linked_esphome_entry(hass, linked_runtime_data)
    _add_linked_esphome_device(hass, linked_entry, connection=("mac", "98:a3:16:e0:f2:e4"))
    transport = HomeAssistantESPHomeTransport(
        hass,
        linked_entry_id=linked_entry.entry_id,
        controller_id=f"esphome:{linked_entry.entry_id}",
        debug_logging_enabled=False,
    )

    await transport.connect()
    await transport.configure_radio(ESPHomeAPIBackend().radio_config)

    assert transport.can_receive is True


async def test_transport_ignores_rx_event_without_device_id(hass) -> None:
    linked_runtime_data = _fake_esphome_runtime_data()
    linked_entry = _add_linked_esphome_entry(hass, linked_runtime_data)
    _add_linked_esphome_device(hass, linked_entry, connection=("mac", "98:a3:16:e0:f2:e4"))
    transport = HomeAssistantESPHomeTransport(
        hass,
        linked_entry_id=linked_entry.entry_id,
        controller_id=f"esphome:{linked_entry.entry_id}",
        debug_logging_enabled=False,
    )

    await transport.connect()
    await transport.configure_radio(ESPHomeAPIBackend().radio_config)

    hass.bus.async_fire(
        ESPHOME_RX_EVENT_TYPE,
        {
            "schema_version": "1",
            "protocol": "proflame2",
            "event_kind": "rx_packet",
            "payload_hex": "00112233445566778899aabbccddeeff001122334455667788",
            "packet_count": "7",
        },
    )
    await hass.async_block_till_done()

    assert await transport.receive_rx_event(timeout=0.01) is None


async def test_transport_ignores_rx_event_from_non_linked_device_id(hass) -> None:
    linked_runtime_data = _fake_esphome_runtime_data()
    linked_entry = _add_linked_esphome_entry(hass, linked_runtime_data)
    _add_linked_esphome_device(hass, linked_entry, connection=("mac", "98:a3:16:e0:f2:e4"))
    other_entry = _add_linked_esphome_entry(hass, _fake_esphome_runtime_data())
    other_device_id = _add_linked_esphome_device(hass, other_entry, connection=("mac", "98:a3:16:e0:f2:e5"))
    transport = HomeAssistantESPHomeTransport(
        hass,
        linked_entry_id=linked_entry.entry_id,
        controller_id=f"esphome:{linked_entry.entry_id}",
        debug_logging_enabled=False,
    )

    await transport.connect()
    await transport.configure_radio(ESPHomeAPIBackend().radio_config)

    hass.bus.async_fire(
        ESPHOME_RX_EVENT_TYPE,
        {
            ATTR_DEVICE_ID: other_device_id,
            "schema_version": "1",
            "protocol": "proflame2",
            "event_kind": "rx_packet",
            "payload_hex": "00112233445566778899aabbccddeeff001122334455667788",
            "packet_count": "7",
        },
    )
    await hass.async_block_till_done()

    assert await transport.receive_rx_event(timeout=0.01) is None


@pytest.mark.parametrize(
    "event_overrides",
    [
        {"schema_version": "3"},
        {"protocol": "other"},
        {"event_kind": "unsupported"},
        {"payload_hex": ""},
        {"payload_hex": "not-hex"},
    ],
)
async def test_transport_ignores_malformed_rx_events(hass, event_overrides: dict[str, str]) -> None:
    linked_runtime_data = _fake_esphome_runtime_data()
    linked_entry = _add_linked_esphome_entry(hass, linked_runtime_data)
    controller_device_id = _add_linked_esphome_device(hass, linked_entry, connection=("mac", "98:a3:16:e0:f2:e4"))
    transport = HomeAssistantESPHomeTransport(
        hass,
        linked_entry_id=linked_entry.entry_id,
        controller_id=f"esphome:{linked_entry.entry_id}",
        debug_logging_enabled=False,
    )

    await transport.connect()
    await transport.configure_radio(ESPHomeAPIBackend().radio_config)

    event_payload = {
        ATTR_DEVICE_ID: controller_device_id,
        "schema_version": "2",
        "protocol": "proflame2",
        "event_kind": "fifo_capture",
        "payload_hex": "aabbccdd",
        "packet_count": "7",
    }
    event_payload.update(event_overrides)
    hass.bus.async_fire(ESPHOME_RX_EVENT_TYPE, event_payload)
    await hass.async_block_till_done()

    assert await transport.receive_rx_event(timeout=0.01) is None


async def test_transport_close_unregisters_rx_event_listener(hass) -> None:
    linked_runtime_data = _fake_esphome_runtime_data()
    linked_entry = _add_linked_esphome_entry(hass, linked_runtime_data)
    controller_device_id = _add_linked_esphome_device(hass, linked_entry, connection=("mac", "98:a3:16:e0:f2:e4"))
    transport = HomeAssistantESPHomeTransport(
        hass,
        linked_entry_id=linked_entry.entry_id,
        controller_id=f"esphome:{linked_entry.entry_id}",
        debug_logging_enabled=False,
    )

    await transport.connect()
    await transport.configure_radio(ESPHomeAPIBackend().radio_config)
    await transport.close()

    hass.bus.async_fire(
        ESPHOME_RX_EVENT_TYPE,
        {
            ATTR_DEVICE_ID: controller_device_id,
            "schema_version": "1",
            "protocol": "proflame2",
            "event_kind": "rx_packet",
            "payload_hex": "00112233445566778899aabbccddeeff001122334455667788",
            "packet_count": "7",
        },
    )
    await hass.async_block_till_done()

    assert await transport.receive_rx_event(timeout=0.01) is None


async def test_esphome_diagnostics_include_linked_transport_telemetry(hass) -> None:
    linked_runtime_data = _fake_esphome_runtime_data()
    linked_entry = _add_linked_esphome_entry(hass, linked_runtime_data)
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0
    entry = _add_entry(
        hass,
        options={CONF_ESPHOME_ENTRY_ID: linked_entry.entry_id},
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.services.async_call(
        DOMAIN,
        "set_state",
        {
            CONF_POWER: True,
            CONF_FLAME: 1,
        },
        blocking=True,
    )

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    transport_diagnostics = diagnostics["runtime"]["backend_diagnostics"]["transport"]

    assert transport_diagnostics["class"] == "HomeAssistantESPHomeTransport"
    assert transport_diagnostics["linked_entry_id"] == linked_entry.entry_id
    assert transport_diagnostics["action_name"] == "proflame2_tx_stateful"
    assert transport_diagnostics["last_tx_path"] == "cc1101_async_gdo0_msb_first"
    assert transport_diagnostics["last_request_id"] == "proflame2-1"
    assert transport_diagnostics["last_payload_length"] == 25
    assert transport_diagnostics["last_payload_hex"] == (runtime_entry.last_packet.transmission_plan.air_payload.hex())
    assert transport_diagnostics["last_request_repeat_count"] == 5
    assert transport_diagnostics["last_tx_result_text"] == "ok"
    assert transport_diagnostics["last_marcstate_before_tx"] == "0x13"
    assert transport_diagnostics["last_marcstate_after_tx"] == "0x01"
    assert transport_diagnostics["cc1101_partnum"] == "0x00"
    assert transport_diagnostics["cc1101_version"] == "0x14"
    assert transport_diagnostics["rx_no_rf_capture_count"] == 1
    assert transport_diagnostics["rx_incomplete_fifo_count"] == 2
    assert transport_diagnostics["rx_decode_failed_count"] == 3
    assert transport_diagnostics["rx_profile_mismatch_count"] == 4
    assert transport_diagnostics["rx_accepted_packet_count"] == 5
    assert transport_diagnostics["rx_tx_suppressed_count"] == 6
    assert transport_diagnostics["rx_transport_unavailable_count"] == 7
    assert transport_diagnostics["rx_last_rejection_snapshot"] == "stage=decode_failed"


async def test_real_esphome_transport_failure_is_bounded_and_clear(hass) -> None:
    linked_runtime_data = _fake_esphome_runtime_data(execute_error="native api unavailable")
    linked_entry = _add_linked_esphome_entry(hass, linked_runtime_data)
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0
    entry = _add_entry(
        hass,
        options={CONF_ESPHOME_ENTRY_ID: linked_entry.entry_id},
    )

    assert await hass.config_entries.async_setup(entry.entry_id)

    with pytest.raises(HomeAssistantError, match="native api unavailable"):
        await hass.services.async_call(
            DOMAIN,
            "set_state",
            {
                CONF_POWER: True,
                CONF_FLAME: 3,
            },
            blocking=True,
        )


def test_esphome_backend_is_exposed_as_production_ready(monkeypatch) -> None:
    monkeypatch.delenv("PROFLAME2_BUILD", raising=False)
    assert BACKEND_ESPHOME in available_backend_types()

    monkeypatch.setenv("PROFLAME2_BUILD", "prod")

    assert BACKEND_ESPHOME in available_backend_types()
    assert available_backend_types() == (BACKEND_YARDSTICK, BACKEND_ESPHOME)


async def test_esphome_runtime_uses_instance_scoped_controller_id(hass) -> None:
    linked_entry = _add_linked_esphome_entry(hass, _fake_esphome_runtime_data())
    entry = _add_entry(
        hass,
        options={CONF_ESPHOME_ENTRY_ID: linked_entry.entry_id},
    )

    runtime_entry = await async_setup_runtime_entry(hass, entry)

    assert runtime_entry.controller_id == f"esphome:{linked_entry.entry_id}"


async def test_esphome_runtime_links_fireplace_device_via_linked_controller(hass) -> None:
    linked_entry = _add_linked_esphome_entry(hass, _fake_esphome_runtime_data())
    controller_device_id = _add_linked_esphome_device(hass, linked_entry, identifier=("esphome", "lilygo-controller-1"))
    entry = _add_entry(
        hass,
        options={CONF_ESPHOME_ENTRY_ID: linked_entry.entry_id},
    )

    runtime_entry = await async_setup_runtime_entry(hass, entry)

    assert runtime_entry.controller_device_id == controller_device_id
    assert runtime_entry.controller_device_identifier == ("esphome", "lilygo-controller-1")
    registry = dr.async_get(hass)
    fireplace_device = registry.async_get(runtime_entry.device_id)
    assert fireplace_device is not None
    assert fireplace_device.identifiers == {(DOMAIN, f"fireplace:{entry.entry_id}")}
    assert fireplace_device.via_device_id == controller_device_id


async def test_esphome_runtime_links_via_controller_device_id_without_identifiers(hass) -> None:
    linked_entry = _add_linked_esphome_entry(hass, _fake_esphome_runtime_data())
    controller_device_id = _add_linked_esphome_device(hass, linked_entry, connection=("mac", "AA:BB:CC:DD:EE:FF"))
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=linked_entry.entry_id,
        identifiers={("demo", "child-device")},
        manufacturer="Demo",
        model="Child",
        name="Child Device",
    )
    entry = _add_entry(
        hass,
        options={CONF_ESPHOME_ENTRY_ID: linked_entry.entry_id},
    )

    runtime_entry = await async_setup_runtime_entry(hass, entry)

    assert runtime_entry.controller_device_id == controller_device_id
    assert runtime_entry.controller_device_identifier is None
    fireplace_device = registry.async_get(runtime_entry.device_id)
    assert fireplace_device is not None
    assert fireplace_device.via_device_id == controller_device_id


async def test_esphome_display_sync_reapplies_active_listener_policy(hass) -> None:
    linked_runtime_data = _fake_esphome_runtime_data()
    linked_entry = _add_linked_esphome_entry(hass, linked_runtime_data)
    entry = _add_entry(
        hass,
        options={
            CONF_ESPHOME_ENTRY_ID: linked_entry.entry_id,
            CONF_ACTIVE_LISTENING: True,
        },
    )

    runtime_entry = await async_setup_runtime_entry(hass, entry)
    linked_runtime_data.active_listening_enabled = False
    linked_runtime_data.active_listening_profile = None

    from custom_components.proflame2.services import async_sync_runtime_display_state

    await async_sync_runtime_display_state(hass, runtime_entry, force=True)

    assert linked_runtime_data.active_listening_enabled is True
    assert linked_runtime_data.active_listening_profile == {
        "serial_id": 0x3B3F02,
        "c1": 5,
        "d1": 7,
        "c2": 1,
        "d2": 8,
    }
    assert runtime_entry.active_listener_task is not None
    assert not runtime_entry.active_listener_task.done()
    runtime_entry.active_listening_enabled = False
    runtime_entry.active_listener_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await runtime_entry.active_listener_task


async def test_active_listener_retries_when_linked_esphome_not_ready(hass) -> None:
    class _Backend:
        def __init__(self) -> None:
            self.calls = 0

        async def receive(self, *, timeout=None):
            del timeout
            self.calls += 1
            raise RuntimeError(
                "ESPHome backend is unavailable: Linked ESPHome entry is not loaded "
                "or has no runtime_data: linked-entry"
            )

    transport = MockESPHomeTransport()
    _install_mock_esphome_transport(hass, transport)
    entry = _add_entry(hass)
    runtime_entry = await async_setup_runtime_entry(hass, entry)
    backend = _Backend()
    runtime_entry.backend = backend
    runtime_entry.active_listening_enabled = True

    await async_start_active_listener(hass, runtime_entry)
    await asyncio.sleep(0.03)

    assert runtime_entry.active_listener_task is not None
    assert not runtime_entry.active_listener_task.done()
    assert backend.calls >= 1
    runtime_entry.active_listening_enabled = False
    runtime_entry.active_listener_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await runtime_entry.active_listener_task


async def test_esphome_runtime_can_link_later_when_controller_device_appears(hass) -> None:
    linked_entry = _add_linked_esphome_entry(hass, _fake_esphome_runtime_data())
    entry = _add_entry(
        hass,
        options={CONF_ESPHOME_ENTRY_ID: linked_entry.entry_id},
    )

    runtime_entry = await async_setup_runtime_entry(hass, entry)

    assert runtime_entry.controller_device_id is None
    assert runtime_entry.controller_device_identifier is None
    registry = dr.async_get(hass)
    fireplace_device = registry.async_get(runtime_entry.device_id)
    assert fireplace_device is not None
    assert fireplace_device.via_device_id is None
    assert async_refresh_runtime_device_link(hass, runtime_entry, entry) is False

    controller_device_id = _add_linked_esphome_device(hass, linked_entry, connection=("mac", "AA:BB:CC:DD:EE:99"))

    linked = await async_retry_runtime_device_link(
        hass,
        runtime_entry,
        entry,
        delays=(0,),
    )

    assert linked is True
    assert runtime_entry.controller_device_id == controller_device_id
    assert runtime_entry.controller_device_identifier is None
    fireplace_device = registry.async_get(runtime_entry.device_id)
    assert fireplace_device is not None
    assert fireplace_device.via_device_id == controller_device_id


async def test_multiple_esphome_entries_keep_distinct_controller_links(hass) -> None:
    linked_entry_one = _add_linked_esphome_entry(hass, _fake_esphome_runtime_data())
    linked_entry_two = _add_linked_esphome_entry(hass, _fake_esphome_runtime_data())
    controller_device_id_one = _add_linked_esphome_device(
        hass, linked_entry_one, connection=("mac", "AA:BB:CC:DD:EE:01")
    )
    controller_device_id_two = _add_linked_esphome_device(
        hass, linked_entry_two, connection=("mac", "AA:BB:CC:DD:EE:02")
    )
    entry_one = _add_entry(
        hass,
        title="Living Room",
        remote_id=0x3B3F02,
        options={CONF_ESPHOME_ENTRY_ID: linked_entry_one.entry_id},
    )
    entry_two = _add_entry(
        hass,
        title="Bedroom",
        remote_id=0x5A4101,
        options={CONF_ESPHOME_ENTRY_ID: linked_entry_two.entry_id},
    )

    runtime_entry_one = await async_setup_runtime_entry(hass, entry_one)
    runtime_entry_two = await async_setup_runtime_entry(hass, entry_two)

    assert runtime_entry_one.controller_device_id == controller_device_id_one
    assert runtime_entry_two.controller_device_id == controller_device_id_two
    assert runtime_entry_one.controller_device_identifier is None
    assert runtime_entry_two.controller_device_identifier is None


async def test_yardstick_runtime_does_not_set_controller_link(hass, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_connect(self):
        return None

    monkeypatch.setattr(YardStickBackend, "connect", fake_connect)
    entry = _add_entry(hass, backend_type=BACKEND_YARDSTICK)

    runtime_entry = await async_setup_runtime_entry(hass, entry)

    assert runtime_entry.controller_device_id is None
    assert runtime_entry.controller_device_identifier is None
    registry = dr.async_get(hass)
    fireplace_device = registry.async_get(runtime_entry.device_id)
    assert fireplace_device is not None
    assert fireplace_device.via_device_id is None
