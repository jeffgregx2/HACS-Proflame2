# LilyGO Firmware Cleanup Backlog

This is an internal backlog for non-feature cleanup work in the LilyGO
ESPHome component. Each item should be handled in a focused change set so that
mechanical cleanup does not obscure RF or controller behavior changes.

## Formatting Baseline

- Establish a repository-approved `clang-format` baseline for
  `esphome/components/proflame2_tembed`.
- Apply the formatter in a formatting-only commit and review it separately
  from functional changes.
- Enable `make format-cpp-check` in CI after the baseline is clean.

## Component Decomposition

- Review the size and mixed responsibilities of `proflame2_tembed.cpp` and
  `proflame2_tembed.h`.
- Move coherent responsibilities into focused modules without changing the
  ESPHome API contract or radio behavior. Candidate boundaries include:
  - component setup, configuration, and board-pin handling;
  - active-listener lifecycle and receive-path selection;
  - FIFO capture and diagnostic export;
  - RMT pulse-capture lifecycle and diagnostics;
  - display and API-client connection state;
  - telemetry publication and status text.
- Keep protocol decoding, CC1101 control, and TX scheduling in their existing
  specialized modules unless the ownership review identifies a concrete gap.
- Add or preserve focused host tests for extracted logic before moving it.

## Diagnostics And Logging

- Review firmware log levels and rate limiting after the receive-path and
  radio-band work.
- Keep useful startup identity logs, including firmware version and selected
  RF band, while avoiding repeated idle-capture noise.
- Consolidate duplicated diagnostic formatting where it improves maintainability
  without reducing support evidence.

## Validation Maintenance

- Keep the host C++ coverage harness current as dependency-free firmware logic
  is extracted.
- Keep 315 MHz and 433.92 MHz ESPHome fixtures compiling in CI.
- Periodically update the validation environment and address ESPHome
  deprecation warnings before their removal deadlines.
