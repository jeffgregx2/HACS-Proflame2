.PHONY: test firmware-unit-test lint-python format-python-check format-cpp-check esphome-setup esphome-stage esphome-config esphome-config-433 esphome-config-invalid-rf-band esphome-compile esphome-compile-433 esphome-validate esphome-clean

# Each ESPHome fixture shares a staged directory. Keep the validation fixtures
# serial even when a caller invokes make with -j.
.NOTPARALLEL: esphome-validate

PYTHON ?= ./.venv/bin/python
BLACK ?= ./.venv/bin/black
RUFF ?= ./.venv/bin/ruff
CLANG_FORMAT ?= ./.venv/bin/clang-format
ESPHOME_PYTHON ?= ./.venv-esphome/bin/python
ESPHOME_CLI ?= ./.venv-esphome/bin/esphome
ESPHOME_EXAMPLE ?= esphome/validate_display_preset.yaml
ESPHOME_STAGE_EXAMPLE ?= validate_display_preset.yaml
ESPHOME_433_FIXTURE ?= tests/fixtures/esphome/validate_display_preset_433.yaml
ESPHOME_STAGE_433_EXAMPLE ?= validate_display_preset_433.yaml
ESPHOME_INVALID_RF_BAND_FIXTURE ?= tests/fixtures/esphome/validate_display_preset_invalid_rf_band.yaml
ESPHOME_STAGE_INVALID_RF_BAND_EXAMPLE ?= validate_display_preset_invalid_rf_band.yaml
ESPHOME_WORK_ROOT ?= /tmp/proflame2-esphome
ESPHOME_STAGE ?= $(ESPHOME_WORK_ROOT)/repo
ESPHOME_STAGE_CLI ?= $(ESPHOME_WORK_ROOT)/esphome

test:
	$(PYTHON) -m pytest -q

firmware-unit-test:
	$(PYTHON) -m pytest -q tests/test_esphome_cpp_unit.py

lint-python:
	$(RUFF) check custom_components tools tests

format-python-check:
	$(BLACK) --check custom_components tools tests

format-cpp-check:
	$(CLANG_FORMAT) --dry-run --Werror esphome/components/proflame2_tembed/*.cpp esphome/components/proflame2_tembed/*.h

esphome-setup:
	test -x $(ESPHOME_PYTHON) || python3 -m venv .venv-esphome
	$(ESPHOME_PYTHON) -m pip install -r requirements-esphome.txt

esphome-stage:
	rm -rf "$(ESPHOME_WORK_ROOT)"
	mkdir -p "$(ESPHOME_STAGE)"
	cp -R esphome "$(ESPHOME_STAGE)/esphome"
	cp "$(ESPHOME_433_FIXTURE)" "$(ESPHOME_STAGE)/esphome/$(ESPHOME_STAGE_433_EXAMPLE)"
	cp "$(ESPHOME_INVALID_RF_BAND_FIXTURE)" "$(ESPHOME_STAGE)/esphome/$(ESPHOME_STAGE_INVALID_RF_BAND_EXAMPLE)"
	ln -s ../components "$(ESPHOME_STAGE)/esphome/examples/components"
	ln -s "$(CURDIR)/.venv-esphome/bin/esphome" "$(ESPHOME_STAGE_CLI)"

esphome-config: esphome-setup esphome-stage
	cd "$(ESPHOME_STAGE)/esphome" && "$(ESPHOME_STAGE_CLI)" config $(ESPHOME_STAGE_EXAMPLE)

esphome-config-433: esphome-setup esphome-stage
	cd "$(ESPHOME_STAGE)/esphome" && "$(ESPHOME_STAGE_CLI)" config $(ESPHOME_STAGE_433_EXAMPLE)

esphome-config-invalid-rf-band: esphome-setup esphome-stage
	@if cd "$(ESPHOME_STAGE)/esphome" && "$(ESPHOME_STAGE_CLI)" config $(ESPHOME_STAGE_INVALID_RF_BAND_EXAMPLE); then \
		echo "Expected invalid proflame2_rf_band configuration to fail"; \
		exit 1; \
	fi

esphome-compile: esphome-config
	find "$(ESPHOME_STAGE)/esphome/.esphome/external_components" -type d -path '*/esphome/components/proflame2_tembed' -prune -exec rm -rf {} \; -exec cp -R "$(ESPHOME_STAGE)/esphome/components/proflame2_tembed" {} \;
	cd "$(ESPHOME_STAGE)/esphome" && "$(ESPHOME_STAGE_CLI)" compile $(ESPHOME_STAGE_EXAMPLE)

esphome-compile-433: esphome-config-433
	find "$(ESPHOME_STAGE)/esphome/.esphome/external_components" -type d -path '*/esphome/components/proflame2_tembed' -prune -exec rm -rf {} \; -exec cp -R "$(ESPHOME_STAGE)/esphome/components/proflame2_tembed" {} \;
	cd "$(ESPHOME_STAGE)/esphome" && "$(ESPHOME_STAGE_CLI)" compile $(ESPHOME_STAGE_433_EXAMPLE)

esphome-validate: esphome-config esphome-config-433 esphome-config-invalid-rf-band esphome-compile esphome-compile-433

esphome-clean:
	rm -rf "$(ESPHOME_WORK_ROOT)"
	rm -rf .esphome esphome/.esphome esphome/examples/.esphome
