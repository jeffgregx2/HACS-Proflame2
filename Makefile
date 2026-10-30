.PHONY: test firmware-unit-test lint-python format-python-check format-cpp-check esphome-setup esphome-config esphome-config-433 esphome-config-invalid-rf-band esphome-compile esphome-compile-433 esphome-validate esphome-clean _esphome-stage _esphome-config _esphome-config-433 _esphome-config-invalid-rf-band _esphome-compile _esphome-compile-433 _esphome-validate

# Each ESPHome fixture shares a staged directory. Keep the validation fixtures
# serial even when a caller invokes make with -j.
.NOTPARALLEL: _esphome-validate

TEST_BOOTSTRAP_PYTHON ?= python3
PYTEST_ARGS ?=
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
# Set only by the cleanup wrapper; direct internal targets must not share /tmp.
ESPHOME_WORK_ROOT ?=
ESPHOME_STAGE ?= $(ESPHOME_WORK_ROOT)/repo
ESPHOME_STAGE_CLI ?= $(ESPHOME_WORK_ROOT)/esphome

test:
	TEST_BOOTSTRAP_PYTHON="$(TEST_BOOTSTRAP_PYTHON)" bash tools/run_tests.sh $(PYTEST_ARGS)

firmware-unit-test:
	TEST_BOOTSTRAP_PYTHON="$(TEST_BOOTSTRAP_PYTHON)" bash tools/run_tests.sh $(PYTEST_ARGS) tests/test_esphome_cpp_unit.py tests/test_radio_register_trace.py

lint-python:
	$(RUFF) check custom_components tools tests

format-python-check:
	$(BLACK) --check custom_components tools tests

format-cpp-check:
	$(CLANG_FORMAT) --dry-run --Werror esphome/components/proflame2_tembed/*.cpp esphome/components/proflame2_tembed/*.h

esphome-setup:
	test -x $(ESPHOME_PYTHON) || python3 -m venv .venv-esphome
	$(ESPHOME_PYTHON) -m pip install -r requirements-esphome.txt

esphome-config esphome-config-433 esphome-config-invalid-rf-band esphome-compile esphome-compile-433 esphome-validate: esphome-setup
	+bash tools/run_esphome_validation.sh _$@

_esphome-stage:
	test -n "$(ESPHOME_WORK_ROOT)"
	mkdir -p "$(ESPHOME_STAGE)"
	cp -R esphome "$(ESPHOME_STAGE)/esphome"
	cp "$(ESPHOME_433_FIXTURE)" "$(ESPHOME_STAGE)/esphome/$(ESPHOME_STAGE_433_EXAMPLE)"
	cp "$(ESPHOME_INVALID_RF_BAND_FIXTURE)" "$(ESPHOME_STAGE)/esphome/$(ESPHOME_STAGE_INVALID_RF_BAND_EXAMPLE)"
	ln -s ../components "$(ESPHOME_STAGE)/esphome/examples/components"
	ln -s "$(abspath $(ESPHOME_CLI))" "$(ESPHOME_STAGE_CLI)"

_esphome-config: _esphome-stage
	cd "$(ESPHOME_STAGE)/esphome" && "$(ESPHOME_STAGE_CLI)" config $(ESPHOME_STAGE_EXAMPLE)

_esphome-config-433: _esphome-stage
	cd "$(ESPHOME_STAGE)/esphome" && "$(ESPHOME_STAGE_CLI)" config $(ESPHOME_STAGE_433_EXAMPLE)

_esphome-config-invalid-rf-band: _esphome-stage
	@if cd "$(ESPHOME_STAGE)/esphome" && "$(ESPHOME_STAGE_CLI)" config $(ESPHOME_STAGE_INVALID_RF_BAND_EXAMPLE); then \
		echo "Expected invalid proflame2_rf_band configuration to fail"; \
		exit 1; \
	fi

_esphome-compile: _esphome-config
	find "$(ESPHOME_STAGE)/esphome/.esphome/external_components" -type d -path '*/esphome/components/proflame2_tembed' -prune -exec rm -rf {} \; -exec cp -R "$(ESPHOME_STAGE)/esphome/components/proflame2_tembed" {} \;
	cd "$(ESPHOME_STAGE)/esphome" && "$(ESPHOME_STAGE_CLI)" compile $(ESPHOME_STAGE_EXAMPLE)

_esphome-compile-433: _esphome-config-433
	find "$(ESPHOME_STAGE)/esphome/.esphome/external_components" -type d -path '*/esphome/components/proflame2_tembed' -prune -exec rm -rf {} \; -exec cp -R "$(ESPHOME_STAGE)/esphome/components/proflame2_tembed" {} \;
	cd "$(ESPHOME_STAGE)/esphome" && "$(ESPHOME_STAGE_CLI)" compile $(ESPHOME_STAGE_433_EXAMPLE)

_esphome-validate: _esphome-config _esphome-config-433 _esphome-config-invalid-rf-band _esphome-compile _esphome-compile-433

esphome-clean:
	rm -rf .esphome esphome/.esphome esphome/examples/.esphome
