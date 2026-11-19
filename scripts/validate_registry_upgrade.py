"""One-off, disk-backed HA registry upgrade check using synthetic configuration.

Run with --from-python and --to-python pointing at isolated HA test environments.
No live HA configuration, service calls, network connections or RF are used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def phase(directory: Path, mode: str) -> None:
    from homeassistant import const as ha_const
    from homeassistant.helpers import area_registry, category_registry, floor_registry, issue_registry, label_registry
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry, async_test_home_assistant

    from custom_components.proflame2 import _async_migrate_registry_identity
    from custom_components.proflame2.button import Proflame2ProfileButtonEntity
    from custom_components.proflame2.const import BACKEND_ESPHOME, BACKEND_YARDSTICK, CONF_ESPHOME_ENTRY_ID, DOMAIN
    from custom_components.proflame2.device_registry_compat import device_registry_compatibility
    from custom_components.proflame2.number import Proflame2NumberEntity
    from custom_components.proflame2.runtime import _create_fireplace_device, async_refresh_runtime_device_link
    from custom_components.proflame2.sensor import _Proflame2BaseSensor
    from custom_components.proflame2.switch import Proflame2SwitchEntity

    expected_version = (2025, 8) if mode == "seed" else (2026, 10)
    assert (ha_const.MAJOR_VERSION, ha_const.MINOR_VERSION) == expected_version, ha_const.__version__
    assert (directory / "synthetic-test-workspace").read_text() == "proflame2-registry-upgrade\n"
    config = directory / "config"
    config.mkdir(exist_ok=True)
    snapshot_path = directory / "baseline.json"

    async with async_test_home_assistant(config_dir=str(config), load_registries=False) as hass:
        # Use actual HA stores and migration routines, not pytest's in-memory
        # registry fixtures. The helper only creates the isolated HA core object.
        await hass.config_entries.async_initialize()
        if mode != "seed":
            dr.async_setup(hass)
        await asyncio.gather(
            *(
                module.async_load(hass)
                for module in (area_registry, category_registry, dr, er, floor_registry, issue_registry, label_registry)
            )
        )
        devices, entities = dr.async_get(hass), er.async_get(hass)
        compat = device_registry_compatibility(devices)

        if mode == "seed":
            for index, backend in enumerate((BACKEND_ESPHOME, BACKEND_ESPHOME, BACKEND_YARDSTICK)):
                options = {}
                if backend == BACKEND_ESPHOME:
                    controller_entry = MockConfigEntry(domain="esphome", title=f"Synthetic controller {index}")
                    controller_entry.add_to_hass(hass)
                    options[CONF_ESPHOME_ENTRY_ID] = controller_entry.entry_id
                    identity = (
                        {"identifiers": {("esphome", "synthetic-controller")}}
                        if index == 0
                        else {"connections": {("mac", "02:00:00:00:00:02")}}
                    )
                    controller = devices.async_get_or_create(
                        config_entry_id=controller_entry.entry_id, name=controller_entry.title, **identity
                    )
                    entities.async_get_or_create(
                        "sensor",
                        "esphome",
                        f"synthetic-firmware-{index}",
                        config_entry=controller_entry,
                        device_id=controller.id,
                        suggested_object_id=f"controller_{index}_firmware",
                    )
                entry = MockConfigEntry(
                    domain=DOMAIN,
                    title=f"Synthetic fireplace {index}",
                    data={"backend_type": backend, "remote_id": 0x123400 + index},
                    options=options,
                )
                entry.add_to_hass(hass)
            foreign = MockConfigEntry(domain="test", title="Unrelated device")
            foreign.add_to_hass(hass)
            foreign_device = devices.async_get_or_create(
                config_entry_id=foreign.entry_id, identifiers={("test", "unrelated")}, name=foreign.title
            )
            entities.async_get_or_create(
                "sensor", "test", "unrelated", config_entry=foreign, device_id=foreign_device.id
            )

        def snapshot():
            return {
                "entries": {
                    entry.entry_id: {"domain": entry.domain, "data": dict(entry.data), "options": dict(entry.options)}
                    for entry in hass.config_entries.async_entries()
                },
                "devices": {
                    device.id: {
                        "owners": sorted(compat.config_entry_ids(device)),
                        "parent": device.via_device_id,
                        "identifiers": sorted([list(value) for value in device.identifiers]),
                        "connections": sorted([list(value) for value in device.connections]),
                        "name": device.name,
                    }
                    for entry in hass.config_entries.async_entries()
                    for device in dr.async_entries_for_config_entry(devices, entry.entry_id)
                },
                "entities": {
                    entity.entity_id: {
                        "id": entity.id,
                        "unique_id": entity.unique_id,
                        "device_id": entity.device_id,
                        "config_entry_id": entity.config_entry_id,
                        "disabled_by": entity.disabled_by,
                    }
                    for entity in entities.entities.values()
                },
            }

        if mode != "seed":
            baseline = json.loads(snapshot_path.read_text())
            assert snapshot() == baseline, "HA storage migration changed identities, ownership or parent links"

        for entry in hass.config_entries.async_entries(DOMAIN):
            await _async_migrate_registry_identity(hass, entry)
            backend = entry.data["backend_type"]
            device = _create_fireplace_device(hass, entry, fireplace_id=entry.entry_id, backend_type=backend)
            runtime = SimpleNamespace(
                config_entry_id=entry.entry_id,
                fireplace_id=entry.entry_id,
                title=entry.title,
                backend_type=backend,
                device_id=device.id,
            )
            linked = async_refresh_runtime_device_link(hass, runtime, entry)
            assert linked == (backend == BACKEND_ESPHOME)
            platform_entities = [
                ("sensor", "primary", _Proflame2BaseSensor(runtime)),
                ("switch", "power_control", Proflame2SwitchEntity(runtime, "power", "Power", "mdi:fire")),
                ("number", "flame_control", Proflame2NumberEntity(runtime, "flame", "Flame", 1, 6, "mdi:fire")),
                (
                    "button",
                    "low_profile_button",
                    Proflame2ProfileButtonEntity(runtime_entry=runtime, profile_id="low", profile_name="Low"),
                ),
            ]
            for domain, key, entity in platform_entities:
                registered = devices.async_get_or_create(config_entry_id=entry.entry_id, **entity.device_info)
                assert registered.id == device.id
                entities.async_get_or_create(
                    domain,
                    DOMAIN,
                    f"{entry.entry_id}_{key}",
                    config_entry=entry,
                    device_id=device.id,
                    suggested_object_id=f"{entry.title}_{key}",
                )
            if backend == BACKEND_YARDSTICK:
                assert devices.async_get(device.id).via_device_id is None
                entities.async_get_or_create(
                    "select", DOMAIN, f"{entry.entry_id}_rf_band", config_entry=entry, device_id=device.id
                )

        current = snapshot()
        if mode == "seed":
            snapshot_path.write_text(json.dumps(current, indent=2, sort_keys=True))
        else:
            assert current == baseline, "Integration reconciliation/registration changed the saved registry"

        # Test-only flush: these HA-private serialization hooks force actual disk
        # writes without waiting for delayed-save timers. They are not integration APIs.
        for registry in (hass.config_entries, devices, entities):
            await registry._store.async_save(registry._data_to_save())
        await hass.async_block_till_done()
        schemas = {}
        for key in ("core.config_entries", "core.device_registry", "core.entity_registry"):
            stored = json.loads((config / ".storage" / key).read_text())
            schemas[key] = [stored["version"], stored["minor_version"]]
        print(
            json.dumps(
                {
                    "phase": mode,
                    "ha": ha_const.__version__,
                    "devices": len(current["devices"]),
                    "entities": len(current["entities"]),
                    "storage_schemas": schemas,
                    "result": "PASS",
                }
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-python", type=Path)
    parser.add_argument("--to-python", type=Path)
    parser.add_argument("--phase", choices=("seed", "upgrade", "reload"), help=argparse.SUPPRESS)
    parser.add_argument("--workspace", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.phase:
        assert args.workspace is not None
        asyncio.run(phase(args.workspace, args.phase))
        return
    if not args.from_python or not args.to_python:
        parser.error("--from-python and --to-python are required")
    with TemporaryDirectory(prefix="proflame2-registry-upgrade-data-") as directory:
        (Path(directory) / "synthetic-test-workspace").write_text("proflame2-registry-upgrade\n")
        for mode, python in (("seed", args.from_python), ("upgrade", args.to_python), ("reload", args.to_python)):
            subprocess.run(
                [
                    str(python.absolute()),
                    "-B",
                    str(Path(__file__).resolve()),
                    "--phase",
                    mode,
                    "--workspace",
                    directory,
                ],
                cwd=ROOT,
                check=True,
                timeout=60,
            )
    print("PASS: persisted HA 2025.8 -> 2026.10 migration, reconciliation and second modern load")


if __name__ == "__main__":
    main()
