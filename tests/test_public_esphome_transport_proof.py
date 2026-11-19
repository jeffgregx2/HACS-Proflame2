"""Public transport regressions promoted from the feasibility proof.

Use real HA public services, registries, states and events with synthetic firmware
telemetry. Existing firmware's queued/ok/error and counter contract is preserved.
All scenarios exercise the production transport; only endpoint/network I/O is synthetic.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import inspect
import itertools
import logging
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

import pytest
import voluptuous as vol
import yaml
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proflame2.rf.esphome.contract import (
    ESPHomeDisplayState,
    ESPHomeRadioConfig,
    ESPHomeTXConfirmationError,
    ESPHomeTXRequest,
)
from custom_components.proflame2.rf.esphome.public_endpoint import ESPHomeControllerOfflineError
from custom_components.proflame2.rf.esphome.transport import HomeAssistantESPHomeTransport

ACTION_NAMES = (
    "proflame2_tx_stateful",
    "proflame2_display_state_update",
    "proflame2_rx_set_active_listening",
    "proflame2_rx_stop",
    "proflame2_rx_end_confirmation",
    "proflame2_learn_mode_update",
)
OPTIONAL_ACTION = "proflame2_rx_end_confirmation"
REQUIRED = (
    "endpoint_status",
    "last_request_id",
    "last_tx_result",
    "last_payload_length",
    "tx_success_count",
    "tx_failure_count",
)
TELEMETRY_NAMES = {
    "endpoint_status": "Proflame2 Endpoint Status",
    "last_request_id": "Proflame2 Last Request ID",
    "last_tx_result": "Proflame2 Last TX Result",
    "last_payload_length": "Proflame2 Last Payload Length",
    "tx_success_count": "Proflame2 TX Success Count",
    "tx_failure_count": "Proflame2 TX Failure Count",
}


async def send(proof, request_id, *, timeout=0.3):
    await proof.hass.async_block_till_done()
    if not proof.connection_is_current:
        await proof.connect()
        await proof.configure_radio(ESPHomeRadioConfig())
    proof.observation_timeout_seconds = timeout
    return (await proof.send_tx(ESPHomeTXRequest(request_id, b"\x55" * 23, 181, 5))).ok


class ForbiddenPrivateRuntime:
    def __getattribute__(self, name):
        raise AssertionError(f"Proof accessed private ESPHome runtime: {name}")


@pytest.fixture(params=("legacy", "modern"))
async def endpoint(hass, request):
    entry = MockConfigEntry(
        domain="esphome",
        title="User-renamed controller",
        data={"device_name": "proof-controller", "host": "192.0.2.10"},
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = ForbiddenPrivateRuntime()
    registry = er.async_get(hass)
    entities = {}
    defaults = {
        "endpoint_status": "ready/rx_supported",
        "last_request_id": "old-request",
        "last_tx_result": "ok",
        "last_payload_length": "23",
        "tx_success_count": "7",
        "tx_failure_count": "0",
    }
    for key, value in defaults.items():
        kind = "sensor" if key in {"last_payload_length", "tx_success_count", "tx_failure_count"} else "text_sensor"
        unique_id = (
            f"001122334455-{kind}-proflame2_{key}"
            if request.param == "legacy"
            else f"02:00:00:00:00:01/0/{kind}/{TELEMETRY_NAMES[key]}"
        )
        entity = registry.async_get_or_create(
            "sensor",
            "esphome",
            unique_id,
            config_entry=entry,
            suggested_object_id=f"unrelated_user_label_{key}",
        )
        entities[key] = entity.entity_id
        hass.states.async_set(entity.entity_id, value)
    calls = []

    @callback
    def handler(call):
        calls.append(call)

    for action in ACTION_NAMES:
        hass.services.async_register("esphome", f"proof_controller_{action}", handler)
    transport = HomeAssistantESPHomeTransport(
        hass, linked_entry_id=entry.entry_id, controller_id="synthetic", debug_logging_enabled=False
    )
    await transport.connect()
    await transport.configure_radio(ESPHomeRadioConfig())
    yield transport, entry, entities, calls
    await transport.close()
    entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


def publish(proof, entities, **fields):
    for key, value in fields.items():
        proof.hass.states.async_set(entities[key], str(value))


async def start_send(proof, **kwargs):
    task = asyncio.create_task(send(proof, "new-request", **kwargs))
    for _ in range(4):
        await asyncio.sleep(0)
    return task


@pytest.mark.parametrize("action", ACTION_NAMES)
async def test_public_transport_routes_all_actions_without_private_runtime(endpoint, action):
    proof, _, _, calls = endpoint
    payload = {"example": "unchanged"}
    await proof._invoke_action(action, payload)
    assert len(calls) == 1
    assert calls[0].domain == "esphome"
    assert calls[0].service == f"proof_controller_{action}"
    assert calls[0].data == payload


@pytest.mark.parametrize(
    "order", list(itertools.permutations(("last_request_id", "last_tx_result", "tx_success_count")))
)
async def test_public_transport_accepts_completion_in_any_entity_order(endpoint, order):
    proof, _, entities, calls = endpoint
    task = await start_send(proof)
    values = {"last_request_id": "new-request", "last_tx_result": "ok", "tx_success_count": 8}
    for key in order:
        publish(proof, entities, **{key: values[key]})
        await proof.hass.async_block_till_done()
    assert await task is True
    assert len(calls) == 1


async def test_public_transport_first_tx_with_unpublished_request_id(endpoint):
    """Released firmware never publishes its initially empty request ID."""
    transport, _, entities, calls = endpoint
    await transport.close()
    publish(
        transport,
        entities,
        last_request_id=STATE_UNKNOWN,
        last_tx_result="none",
        tx_success_count=0,
        tx_failure_count=0,
        last_payload_length=0,
    )
    await transport.hass.async_block_till_done()
    await transport.connect()
    await transport.configure_radio(ESPHomeRadioConfig())
    assert transport.device_available
    task = await start_send(transport)
    assert len(calls) == 1
    assert not task.done()
    publish(transport, entities, last_request_id="new-request", last_tx_result="queued", last_payload_length=23)
    await transport.hass.async_block_till_done()
    assert not task.done()
    publish(transport, entities, tx_success_count=1, last_tx_result="ok")
    assert await task is True


async def test_public_transport_unknown_request_id_never_confirms_tx(endpoint):
    transport, _, entities, calls = endpoint
    publish(transport, entities, last_request_id=STATE_UNKNOWN)
    await transport.hass.async_block_till_done()
    assert transport.device_available
    task = await start_send(transport, timeout=0.02)
    publish(transport, entities, tx_success_count=8)
    with pytest.raises(RuntimeError, match="telemetry did not confirm"):
        await task
    assert len(calls) == 1


@pytest.mark.parametrize(
    "object_id,state",
    [(object_id, STATE_UNAVAILABLE) for object_id in REQUIRED]
    + [(object_id, STATE_UNKNOWN) for object_id in REQUIRED if object_id != "last_request_id"],
)
async def test_public_transport_unavailable_required_entity_prevents_tx(endpoint, object_id, state):
    transport, _, entities, calls = endpoint
    publish(transport, entities, **{object_id: state})
    await transport.hass.async_block_till_done()
    assert not transport.device_available
    with pytest.raises(ESPHomeControllerOfflineError, match="unavailable"):
        await send(transport, "new-request")
    assert not calls


async def test_public_transport_unknown_request_id_event_keeps_live_binding(endpoint):
    transport, _, entities, _ = endpoint
    await transport.hass.async_block_till_done()
    generation = transport._endpoint.generation
    publish(transport, entities, last_request_id=STATE_UNKNOWN)
    await transport.hass.async_block_till_done()
    assert transport.connection_is_current
    assert transport._endpoint.generation == generation


async def test_public_transport_rejects_cached_ok_until_fresh_completion(endpoint):
    proof, _, entities, calls = endpoint
    task = await start_send(proof)
    publish(proof, entities, last_request_id="new-request")
    await proof.hass.async_block_till_done()
    assert not task.done(), "A new request ID and cached ok are not completion"
    publish(proof, entities, last_tx_result="queued")
    await proof.hass.async_block_till_done()
    assert not task.done()
    publish(proof, entities, tx_success_count=8, last_tx_result="ok")
    assert await task is True
    assert len(calls) == 1


async def test_public_transport_rejects_another_request_and_accepts_failure(endpoint):
    proof, _, entities, calls = endpoint
    task = await start_send(proof)
    publish(proof, entities, last_request_id="different-request", tx_success_count=8)
    await proof.hass.async_block_till_done()
    assert not task.done()
    publish(proof, entities, last_request_id="new-request", last_tx_result="error:tx_busy", tx_failure_count=1)
    assert await task is False
    assert len(calls) == 1


async def test_public_transport_receives_completion_during_service_dispatch(endpoint):
    proof, _, entities, _ = endpoint

    @callback
    def handler(_call):
        publish(proof, entities, last_request_id="new-request", tx_success_count=8)

    proof.hass.services.async_register("esphome", "proof_controller_proflame2_tx_stateful", handler)
    assert await send(proof, "new-request") is True


@pytest.mark.parametrize("timeout", [0.0, 0.02])
async def test_public_transport_timeout_has_no_replay_and_ignores_late_completion(endpoint, timeout):
    proof, _, entities, calls = endpoint
    listeners_before = proof.hass.bus.async_listeners().get("state_changed", 0)
    with pytest.raises(ESPHomeTXConfirmationError, match="telemetry did not confirm"):
        await send(proof, "new-request", timeout=timeout)
    publish(proof, entities, last_request_id="new-request", tx_success_count=8)
    await proof.hass.async_block_till_done()
    assert len(calls) == 1
    assert proof.hass.bus.async_listeners().get("state_changed", 0) == listeners_before


async def test_public_transport_cancellation_releases_listener_without_replay(endpoint):
    proof, _, _, calls = endpoint
    listeners_before = proof.hass.bus.async_listeners().get("state_changed", 0)
    task = await start_send(proof)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) == 1
    assert proof.hass.bus.async_listeners().get("state_changed", 0) == listeners_before


@pytest.mark.parametrize("state", [STATE_UNAVAILABLE, STATE_UNKNOWN, None])
async def test_public_transport_offline_and_quiet_recovery(endpoint, state):
    proof, _, entities, calls = endpoint
    assert proof.device_available
    if state is None:
        proof.hass.states.async_remove(entities["endpoint_status"])
    else:
        publish(proof, entities, endpoint_status=state)
    assert not proof.device_available
    with pytest.raises(RuntimeError, match="unavailable"):
        await send(proof, "new-request")
    assert not calls
    publish(proof, entities, endpoint_status="ready/rx_supported")
    assert proof.device_available
    task = await start_send(proof)
    publish(proof, entities, last_request_id="new-request", tx_success_count=8)
    assert await task


async def test_public_transport_disconnect_while_waiting_fails_without_resend(endpoint):
    proof, _, entities, calls = endpoint
    task = await start_send(proof)
    publish(proof, entities, endpoint_status=STATE_UNAVAILABLE)
    with pytest.raises(RuntimeError, match="unavailable"):
        await task
    assert len(calls) == 1


async def test_public_transport_entity_rename_rebinds_by_unique_id(endpoint):
    proof, _, entities, _ = endpoint
    old_id = entities["last_request_id"]
    er.async_get(proof.hass).async_update_entity(old_id, new_entity_id="sensor.completely_renamed")
    proof.hass.states.async_remove(old_id)
    proof.hass.states.async_set("sensor.completely_renamed", "old-request")
    proof._endpoint.resolve()
    assert proof._endpoint.entities["proflame2_last_request_id"] == "sensor.completely_renamed"
    assert proof.device_available


@pytest.mark.parametrize("version", [1, 2, 3])
async def test_public_transport_survives_esphome_unique_id_migration(endpoint, version):
    """An active ESPHome startup migration changes IDs, not just HA storage."""
    api = pytest.importorskip("aioesphomeapi")
    if not hasattr(api, "build_device_unique_id"):
        pytest.skip("This API version predates the v3 ESPHome unique ID migration")
    transport, _, entities, calls = endpoint
    registry = er.async_get(transport.hass)
    for index, (key, entity_id) in enumerate(entities.items()):
        info_type = (
            api.SensorInfo
            if key in {"last_payload_length", "tx_success_count", "tx_failure_count"}
            else api.TextSensorInfo
        )
        name = TELEMETRY_NAMES[key]
        info = info_type(key=index, name=name, object_id=f"proflame2_{key}")
        unique_id = api.build_device_unique_id("02:00:00:00:00:01", info, version=version)
        assert ("/" in unique_id) == (version == 3)
        if registry.async_get(entity_id).unique_id != unique_id:
            registry.async_update_entity(entity_id, new_unique_id=unique_id)
    await transport.hass.async_block_till_done()
    await transport.connect()
    await transport.configure_radio(ESPHomeRadioConfig())
    task = await start_send(transport)
    publish(transport, entities, last_request_id="new-request", tx_success_count=8)
    assert await task is True
    assert len(calls) == 1
    assert transport._endpoint.entities["proflame2_last_request_id"] == entities["last_request_id"]


@pytest.mark.parametrize("object_id", ["tx_success_count", "last_request_id"])
@pytest.mark.parametrize("problem", ["disabled", "missing", "ambiguous"])
async def test_public_transport_required_entity_problems_fail_safely(endpoint, problem, object_id):
    proof, entry, entities, calls = endpoint
    registry = er.async_get(proof.hass)
    if problem == "disabled":
        registry.async_update_entity(entities[object_id], disabled_by=er.RegistryEntryDisabler.USER)
    elif problem == "missing":
        registry.async_remove(entities[object_id])
    else:
        registry.async_get_or_create(
            "sensor", "esphome", f"a-different-device-proflame2_{object_id}", config_entry=entry
        )
    assert not proof.device_available
    with pytest.raises(RuntimeError, match="telemetry"):
        await send(proof, "new-request")
    assert not calls


async def test_public_transport_other_controller_entities_and_actions_not_selected(endpoint):
    proof, _, _, calls = endpoint
    other = MockConfigEntry(domain="esphome", data={"device_name": "other-controller"})
    other.add_to_hass(proof.hass)
    for key in REQUIRED:
        entity = er.async_get(proof.hass).async_get_or_create(
            "sensor", "esphome", f"different-mac-proflame2_{key}", config_entry=other
        )
        proof.hass.states.async_set(entity.entity_id, "unavailable")
    wrong_calls = []

    @callback
    def handler(call):
        wrong_calls.append(call)

    proof.hass.services.async_register("esphome", "other_controller_proflame2_rx_stop", handler)
    await proof._invoke_action("proflame2_rx_stop", {})
    assert len(calls) == 1
    assert not wrong_calls


async def test_public_transport_missing_action_waits_for_registration(endpoint):
    proof, _, _, calls = endpoint
    name = "proof_controller_proflame2_rx_stop"
    proof.hass.services.async_remove("esphome", name)
    assert proof.device_available  # TX remains usable; this is an RX capability.

    @callback
    def handler(call):
        calls.append(call)

    proof.hass.services.async_register("esphome", name, handler)
    assert proof.device_available
    await proof._invoke_action("proflame2_rx_stop", {})
    assert len(calls) == 1


async def test_public_transport_public_service_error_not_silenced_or_retried(endpoint):
    proof, _, _, _ = endpoint

    @callback
    def handler(_call):
        raise HomeAssistantError("synthetic disconnect")

    proof.hass.services.async_register("esphome", "proof_controller_proflame2_tx_stateful", handler)
    with pytest.raises(HomeAssistantError, match="synthetic disconnect"):
        await send(proof, "new-request")


INTEGRATION_ROOT = Path(__file__).resolve().parents[1] / "custom_components/proflame2"


def private_ha_accesses(source):
    """Detect forbidden cross-integration dependencies, including dynamic reads.

    Test doubles and HA's own handler contract checks are intentionally outside
    this production boundary. Proflame2's own private methods remain allowed.
    """
    tree = ast.parse(source)
    ha_names = {"hass"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("homeassistant"):
            ha_names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            ha_names.update(
                alias.asname or alias.name.split(".")[0]
                for alias in node.names
                if alias.name.startswith("homeassistant")
            )
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            root = node.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in ha_names:
                violations.append((node.lineno, "private HA attribute"))
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for module in modules:
                if (module == "homeassistant" or module.startswith("homeassistant.")) and (
                    module.startswith("homeassistant.components.esphome")
                    or any(part.startswith("_") for part in module.split("."))
                    or any(alias.name.startswith("_") for alias in node.names)
                ):
                    violations.append((node.lineno, "private HA import"))
        if isinstance(node, ast.Attribute) and node.attr in {"runtime_data", "execute_service"}:
            violations.append((node.lineno, node.attr))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"getattr", "hasattr"}:
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                if node.args[1].value in {"runtime_data", "execute_service"}:
                    violations.append((node.lineno, "dynamic private runtime access"))
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "data":
            if isinstance(node.slice, ast.Constant) and node.slice.value == "esphome":
                violations.append((node.lineno, "private ESPHome data store"))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"get", "setdefault"}
        ):
            store = node.func.value
            if isinstance(store, ast.Attribute) and store.attr == "data" and node.args:
                if isinstance(node.args[0], ast.Constant) and node.args[0].value == "esphome":
                    violations.append((node.lineno, "private ESPHome data store"))
    return violations


@pytest.mark.parametrize(
    "path", sorted(INTEGRATION_ROOT.rglob("*.py")), ids=lambda path: str(path.relative_to(INTEGRATION_ROOT))
)
def test_production_integration_contains_no_private_esphome_access(path):
    assert private_ha_accesses(path.read_text()) == []


@pytest.mark.parametrize(
    "source",
    [
        "from homeassistant.components.esphome import RuntimeData as Renamed",
        "import homeassistant.components.esphome.manager as manager",
        "from homeassistant.helpers.entity_registry import _internal",
        "from homeassistant import core as renamed; renamed._internal()",
        "hass._private_state",
        "entry.runtime_data.client",
        "client.execute_service(service, data)",
        "getattr(entry, 'runtime_data')",
        "hasattr(client, 'execute_service')",
        "hass.data['esphome']",
        "hass.data.get('esphome')",
        "hass.data.setdefault('esphome', {})",
    ],
)
def test_private_api_guard_detects_forbidden_dependencies(source):
    assert private_ha_accesses(source)


def test_private_api_guard_allows_public_interfaces_and_own_internals():
    assert (
        private_ha_accesses(
            "from homeassistant.helpers import entity_registry as er\n"
            "self._collect_snapshot()\nhass.services.async_call('esphome', name, data)\n"
            "hass.states.get(entity_id)\nhass.data.setdefault('proflame2', {})"
        )
        == []
    )
    assert "asyncio.sleep" not in inspect.getsource(HomeAssistantESPHomeTransport)


def firmware_actions():
    """Read actual installed-firmware action schemas, not hand-written copies."""

    class Loader(yaml.SafeLoader):
        pass

    Loader.add_multi_constructor("!", lambda loader, _tag, node: loader.construct_scalar(node))
    package = Path(__file__).resolve().parents[1] / "esphome/packages/proflame2_tembed_base.yaml"
    return yaml.load(package.read_text(), Loader=Loader)["api"]["actions"]


async def test_public_transport_existing_firmware_optional_action_absent(endpoint):
    proof, _, _, calls = endpoint
    assert OPTIONAL_ACTION not in {item["action"] for item in firmware_actions()}
    proof.hass.services.async_remove("esphome", f"proof_controller_{OPTIONAL_ACTION}")
    assert proof.device_available
    await proof.end_confirmation_rx()
    assert not calls


async def test_public_transport_missing_discovery_and_config_entry_reload(endpoint):
    proof, entry, entities, calls = endpoint
    proof.hass.config_entries.async_update_entry(entry, data={})
    assert not proof.device_available
    proof.hass.config_entries.async_update_entry(entry, data={"device_name": "proof-controller"})
    assert proof.device_available
    # A changed/removed action must not be used just because it was once bound.
    task = await start_send(proof)
    proof.hass.services.async_remove("esphome", "proof_controller_proflame2_tx_stateful")
    publish(proof, entities, tx_success_count=8)
    with pytest.raises(RuntimeError, match="endpoint changed|does not expose"):
        await task
    assert len(calls) == 1


async def test_public_transport_dispatch_overhead_is_local(endpoint, record_property):
    proof, _, _, calls = endpoint
    timings = []
    for _ in range(100):
        start = perf_counter()
        await proof._invoke_action("proflame2_rx_stop", {})
        timings.append((perf_counter() - start) * 1000)
    record_property("public_dispatch_median_ms", sorted(timings)[50])
    record_property("public_dispatch_max_ms", max(timings))
    assert len(calls) == 100


def installed_esphome_handler():
    """Run the installed HA handler verbatim, without unrelated voice imports.

    Importing all of ESPHome also imports native Assist audio modules. Those
    are outside this proof. Extract only these unmodified source functions;
    use real HA services and aioesphomeapi models, never a copied handler.
    """
    api = pytest.importorskip("aioesphomeapi")
    ha_root = Path(importlib.util.find_spec("homeassistant").submodule_search_locations[0])
    path = ha_root / "components/esphome/manager.py"
    nodes = [
        node
        for node in ast.parse(path.read_text()).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"build_service_name", "execute_service"}
    ]
    assert len(nodes) == 2
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes],
        type_ignores=[],
    )
    namespace = {
        "callback": callback,
        "SupportsResponseType": getattr(api, "SupportsResponseType", None),
        "APIConnectionError": api.APIConnectionError,
        "HomeAssistantError": HomeAssistantError,
        "DOMAIN": "esphome",
        "_LOGGER": logging.getLogger(__name__),
    }
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return api, namespace


def native_handler(api, functions, service, native_execute, name):
    """Support the sync (older HA) and async (newer HA) public handlers."""
    execute = functions["execute_service"]
    if inspect.iscoroutinefunction(execute):

        async def async_native(actual_service, data):
            native_execute(actual_service, data)

        client = SimpleNamespace(execute_service=async_native)
    else:
        client = SimpleNamespace(execute_service=native_execute)
    runtime = SimpleNamespace(client=client, name=name)

    async def handler(call):
        kwargs = {}
        if "supports_response" in inspect.signature(execute).parameters:
            kwargs["supports_response"] = api.SupportsResponseType.NONE
        result = execute(runtime, service, call, **kwargs)
        if inspect.isawaitable(result):
            await result

    return runtime, handler


@pytest.mark.parametrize("action", [name for name in ACTION_NAMES if name != OPTIONAL_ACTION])
async def test_public_transport_actual_esphome_handler_preserves_firmware_payload(endpoint, action):
    proof, _, entities, _ = endpoint
    api, functions = installed_esphome_handler()
    declaration = next(item for item in firmware_actions() if item["action"] == action)
    types = {"int": api.UserServiceArgType.INT, "string": api.UserServiceArgType.STRING}
    variables = declaration.get("variables", {})
    service = api.UserService(
        name=action,
        key=123,
        args=[api.UserServiceArg(name=name, type=types[kind]) for name, kind in variables.items()],
    )
    info = api.DeviceInfo(name="proof-controller")
    actual_name = functions["build_service_name"](info, service)
    assert actual_name == f"proof_controller_{action}"
    transmitted = []

    def native_execute(actual_service, data):
        transmitted.append((actual_service, dict(data)))
        if action == "proflame2_tx_stateful":
            publish(proof, entities, last_request_id="new-request", tx_success_count=8)

    # Only the API client's network side is synthetic. The service invocation
    # passes through HA's real registry and its unmodified ESPHome handler.
    runtime, handler = native_handler(api, functions, service, native_execute, info.name)

    schema = vol.Schema({vol.Required(name): int if kind == "int" else str for name, kind in variables.items()})
    proof.hass.services.async_register("esphome", actual_name, handler, schema)
    # Replacing an action invalidates the binding, just as an ESPHome reload
    # does. Let its event run and reconnect before beginning a new operation.
    await proof.hass.async_block_till_done()
    await proof.connect()
    await proof.configure_radio(ESPHomeRadioConfig())
    display = ESPHomeDisplayState(
        fireplace_name="Synthetic fireplace",
        power=True,
        flame=0,
        fan=2,
        light=3,
        pilot=1,
        thermostat=False,
        front=True,
        aux=False,
        action_label="Synthetic action",
        status_text="Sending...",
    )
    if action == "proflame2_tx_stateful":
        response = await proof.send_tx(ESPHomeTXRequest("new-request", b"\x55" * 23, 181, 5, display_state=display))
        assert response.ok
    elif action == "proflame2_display_state_update":
        await proof.update_display_state(display)
    elif action == "proflame2_rx_set_active_listening":
        profile = SimpleNamespace(serial_id=0x123456, ecc=SimpleNamespace(c1=5, d1=7, c2=1, d2=8))
        await proof.set_active_listening(True, profile)
    elif action == "proflame2_rx_stop":
        await proof.stop_rx()
    else:
        assert action == "proflame2_learn_mode_update"
        await proof.update_learning_mode(
            active=True, step_title="Step 1 of 4", instruction="Power ON", status="Listening"
        )

    assert len(transmitted) == 1
    actual_service, payload = transmitted[0]
    assert actual_service is service
    assert set(payload) == set(variables)
    if action in {"proflame2_tx_stateful", "proflame2_display_state_update"}:
        assert payload["intended_flame"] == 0
        assert payload["intended_power"] == 1
        assert payload["intended_pilot"] == 1
        assert payload["intended_thermostat"] == 0
        assert payload["intended_front"] == 1
        assert payload["intended_aux"] == 0
        assert payload["intended_fan"] == 2
        assert payload["intended_light"] == 3
        assert payload["fireplace_name"] == display.fireplace_name
        assert payload["intended_action_label"] == display.action_label
    if action == "proflame2_tx_stateful":
        assert payload["request_id"] == "new-request"
        assert payload["air_payload_hex"] == "55" * 23
        assert payload["payload_bit_length"] == 181
        assert payload["repeat_count"] == 5
    elif action == "proflame2_rx_set_active_listening":
        assert payload == {"enabled": 1, "serial_id": 0x123456, "c1": 5, "d1": 7, "c2": 1, "d2": 8}
    elif action == "proflame2_learn_mode_update":
        assert payload == {"active": 1, "step_title": "Step 1 of 4", "instruction": "Power ON", "status": "Listening"}


async def test_public_transport_actual_esphome_handler_does_not_claim_tx_completion(endpoint):
    proof, _, _, _ = endpoint
    api, functions = installed_esphome_handler()
    service = api.UserService(name="proflame2_tx_stateful")
    invoked = []

    def native_execute(_service, data):
        invoked.append(dict(data))

    runtime, handler = native_handler(api, functions, service, native_execute, "proof-controller")

    proof.hass.services.async_register("esphome", "proof_controller_proflame2_tx_stateful", handler)
    with pytest.raises(RuntimeError, match="telemetry did not confirm"):
        await send(proof, "new-request", timeout=0.02)
    assert len(invoked) == 1
    assert invoked[0]["request_id"] == "new-request"


async def test_public_transport_node_rename_uses_fresh_public_binding(endpoint):
    proof, entry, _, _ = endpoint
    new_calls = []

    @callback
    def handler(call):
        new_calls.append(call)

    for action in ACTION_NAMES:
        proof.hass.services.async_remove("esphome", f"proof_controller_{action}")
        proof.hass.services.async_register("esphome", f"new_node_{action}", handler)
    proof.hass.config_entries.async_update_entry(entry, data={"device_name": "new-node"})
    await proof._invoke_action("proflame2_rx_stop", {})
    assert len(new_calls) == 1
    assert new_calls[0].service == "new_node_proflame2_rx_stop"


async def test_public_transport_firmware_version_uses_renamable_public_entity(endpoint):
    proof, entry, _, _ = endpoint
    registry = er.async_get(proof.hass)
    entity = registry.async_get_or_create(
        "sensor", "esphome", "001122334455-text_sensor-proflame2_firmware_version", config_entry=entry
    )
    registry.async_update_entity(entity.entity_id, new_entity_id="sensor.user_renamed_version")
    proof.hass.states.async_set("sensor.user_renamed_version", "v0.7.1")
    assert proof.proflame2_firmware_version() == "v0.7.1"
    proof.hass.states.async_set("sensor.user_renamed_version", STATE_UNAVAILABLE)
    assert proof.proflame2_firmware_version() is None


async def test_public_transport_rebooted_counter_cannot_confirm_previous_epoch(endpoint):
    proof, _, entities, calls = endpoint
    task = await start_send(proof, timeout=0.02)
    publish(proof, entities, last_request_id="new-request", tx_success_count=1)
    with pytest.raises(RuntimeError, match="counters reset"):
        await task
    assert len(calls) == 1


async def test_production_confirmation_rejects_mixed_cached_ok(endpoint):
    proof, _, entities, calls = endpoint
    task = await start_send(proof, timeout=0.02)
    publish(proof, entities, last_request_id="new-request")
    with pytest.raises(RuntimeError, match="telemetry did not confirm"):
        await task
    assert len(calls) == 1


async def test_public_transport_records_fast_disconnect_and_restore(endpoint):
    transport, _, entities, _ = endpoint
    generation = transport._endpoint.generation
    publish(transport, entities, endpoint_status="unavailable")
    publish(transport, entities, endpoint_status="ready/rx_supported")
    await transport.hass.async_block_till_done()
    assert transport.device_available
    assert transport._endpoint.generation > generation
    assert not transport.connection_is_current


async def test_public_transport_ignores_other_nodes_with_shared_name_prefix(endpoint):
    transport, _, _, _ = endpoint
    generation = transport._endpoint.generation

    @callback
    def handler(_call):
        pass

    transport.hass.services.async_register("esphome", "proof_controller_other_proflame2_tx_stateful", handler)
    await transport.hass.async_block_till_done()
    assert transport._endpoint.generation == generation
    assert transport.connection_is_current


async def test_public_transport_close_cleans_subscriptions(endpoint):
    transport, _, entities, _ = endpoint
    await transport.hass.async_block_till_done()
    await transport.close()
    generation = transport._endpoint.generation
    publish(transport, entities, endpoint_status="unavailable")
    er.async_get(transport.hass).async_update_entity(
        entities["last_request_id"], new_entity_id="sensor.renamed_after_close"
    )
    transport.hass.services.async_remove("esphome", "proof_controller_proflame2_tx_stateful")
    await transport.hass.async_block_till_done()
    assert transport._endpoint.generation == generation
    assert transport._endpoint._unsubscribers == []
    assert transport._endpoint._state_unsub is None
    assert transport._rx_unsub is None
    assert not transport.connected
