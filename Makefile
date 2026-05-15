.PHONY: test lint-python format-python-check format-cpp-check esphome-setup esphome-stage esphome-config esphome-compile esphome-validate esphome-clean

PYTHON ?= ./.venv/bin/python
BLACK ?= ./.venv/bin/black
RUFF ?= ./.venv/bin/ruff
CLANG_FORMAT ?= ./.venv/bin/clang-format
ESPHOME_PYTHON ?= ./.venv-esphome/bin/python
ESPHOME_CLI ?= ./.venv-esphome/bin/esphome
ESPHOME_EXAMPLE ?= esphome/examples/lilygo_cc1101_example.yaml
ESPHOME_STAGE_EXAMPLE ?= examples/lilygo_cc1101_example.yaml
ESPHOME_WORK_ROOT ?= /tmp/proflame2-esphome
ESPHOME_STAGE ?= $(ESPHOME_WORK_ROOT)/repo
ESPHOME_STAGE_CLI ?= $(ESPHOME_WORK_ROOT)/esphome

test:
	$(PYTHON) -m pytest -q

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
	ln -s ../components "$(ESPHOME_STAGE)/esphome/examples/components"
	ln -s "$(CURDIR)/.venv-esphome/bin/esphome" "$(ESPHOME_STAGE_CLI)"

esphome-config: esphome-setup esphome-stage
	cd "$(ESPHOME_STAGE)/esphome" && "$(ESPHOME_STAGE_CLI)" config $(ESPHOME_STAGE_EXAMPLE)

esphome-compile: esphome-setup esphome-stage
	cd "$(ESPHOME_STAGE)/esphome" && "$(ESPHOME_STAGE_CLI)" compile $(ESPHOME_STAGE_EXAMPLE)

esphome-validate: esphome-config esphome-compile

esphome-clean:
	rm -rf "$(ESPHOME_WORK_ROOT)"
	rm -rf .esphome esphome/.esphome esphome/examples/.esphome
