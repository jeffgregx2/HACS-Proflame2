# Code Quality Guidelines

These guidelines define the default standard for Python and C++ code in this
repository. They are based on practices visible in mature projects such as
scikit-learn, FastAPI, Django, Abseil, LLVM, and fmt, with special emphasis on
clear structure and useful in-code documentation.

The goal is not to maximize comments or abstraction. The goal is code that a
senior engineer can navigate, evaluate, debug, and safely change.

## Core Principles

1. Optimize for reader understanding.
   Code is read more often than it is written. Prefer names, structure, and
   control flow that expose intent without forcing readers to reconstruct it
   from implementation details.

2. Make semantic boundaries explicit.
   If an object is raw, decoded, semantic, packet-owned, debug-only, accepted,
   rejected, transient, or persisted, that status should be visible in names,
   types, fields, and documentation.

3. Separate policy from mechanics.
   Acceptance rules, validation rules, retry rules, ownership rules, and
   persistence rules should be explicit and testable. Do not hide important
   policy inside incidental filtering or formatting code.

4. Prefer local clarity over clever generality.
   Add abstraction only when it removes real complexity, protects a boundary,
   or matches an established pattern in the codebase.

5. Let tooling enforce style.
   Formatting and linting should be automated. Human review should focus on
   correctness, maintainability, behavior, and design.

6. Preserve installed-user compatibility.
   Once a feature has shipped, changes must account for existing Home Assistant
   entries, options, learned profiles, ESPHome contracts, service schemas,
   entity names, diagnostics, and persisted data. Do not break existing
   installations unless the project intentionally changes the major version and
   documents the required upgrade path.

7. Use codified lifecycle automation.
   Repository lifecycle operations should use existing GitHub Actions or other
   checked-in automation before manual edits. Version stamping, release
   validation, HACS validation, hassfest, ESPHome validation, and similar
   repeatable operations must follow the repository's workflows when they
   exist. Manual execution is acceptable only for inspection, emergency repair,
   or one-off investigation, and should not replace the canonical workflow.

## Repository Structure

Organize code by domain and responsibility, not by vague utility buckets.

Preferred domain-oriented names:

- `capture`
- `decode`
- `transport`
- `analysis`
- `artifacts`
- `validation`
- `persistence`
- `diagnostics`

Avoid names that become dumping grounds:

- `utils`
- `helpers`
- `common`
- `misc`

Use these only when the contents are genuinely generic and cohesive.

### Python Layout

Python code should use packages and modules that reflect the domain model.

Guidelines:

- Keep importable code inside a package.
- Keep tests in `tests/`, loosely mirroring the package structure.
- Keep scripts or one-off command entrypoints separate from reusable library
  logic.
- Keep docs in `docs/` or in clearly named analysis/report directories.
- Avoid top-level modules that accumulate unrelated behavior.
- Prefer explicit imports over wildcard imports.
- Use `pyproject.toml`, lint config, or existing project tooling where
  available.

### Offline Development Tools

Code under `tools/` is development, validation, capture, replay, and offline
analysis infrastructure. It is not production Home Assistant runtime code.
Apply the same correctness expectations to tools that gate project decisions,
but do not force the same decomposition standard used for production modules.

Guidelines:

- Keep behavior strict for tools that affect validation decisions, including
  capture runners, collectors, quick validation, semantic artifact gates,
  YardStick diagnostic audits, and FIFO semantic replicate stability.
- Prefer clear same-file helper extraction over splitting one workflow into
  many modules. A longer single-file tool is acceptable when it represents one
  coherent command or report pipeline.
- Do not over-refactor historical or exploratory analysis tools. If an
  exploratory tool is obsolete, prefer deleting it or leaving it stable over
  investing in production-grade structure.
- Keep artifact ownership policy explicit wherever a tool interprets raw,
  debug, candidate, heuristic, or semantic artifacts.
- Keep tests for tools that gate capture validity, semantic acceptance, or
  future engineering decisions.
- Generated files such as `__pycache__/` are not source artifacts and should
  not be committed.

When reviewing `tools/`, focus on whether the workflow is understandable,
reproducible, and safe from semantic/debug artifact confusion. Do not require
module splitting unless the tool is reused broadly or has become difficult to
test safely in one file.

### C++ Layout

C++ code should separate public API from implementation.

Guidelines:

- Put public headers in an include-style boundary when the code is library-like.
- Put implementation in `src/` or the established local equivalent.
- Put tests in `test/` or `tests/`.
- Put generated or build output outside source directories.
- Prefer private implementation details in anonymous namespaces, `detail`
  namespaces, or private translation units.
- Public headers should be readable as API documentation.

## Module and File Design

Each file should have a coherent reason to exist.

A good file usually has:

- one primary domain concept or workflow
- a short module/file-level purpose statement when the role is not obvious
- related helpers kept near the code that uses them
- public API near the top or clearly separated from private mechanics
- tests that exercise the file's externally visible behavior

Warning signs:

- unrelated functions accumulating over time
- many concepts sharing generic names like `data`, `result`, or `payload`
- a file where persistence, parsing, validation, orchestration, and reporting
  are all interleaved
- comments required to explain basic control flow

## Function and Class Design

Functions should do one meaningful job at one abstraction level.

Guidelines:

- Keep orchestration functions readable as workflows.
- Move mechanical details into named helpers.
- Name helpers after semantic intent, not implementation trivia.
- Prefer explicit return objects over loosely structured tuples or dictionaries
  when data crosses module boundaries.
- Make failure behavior clear: exception, status object, `None`, empty result,
  or explicit rejected artifact.
- Avoid boolean parameters that obscure call-site meaning. Prefer enums,
  options objects, or keyword-only arguments when practical.

Classes should represent stable domain concepts or resource ownership.

Document non-obvious class-level facts:

- ownership and lifetime
- invariants
- accepted states
- thread/concurrency assumptions
- external resources
- failure policy
- relationship to surrounding system concepts

## Naming

Names should carry semantic meaning.

Prefer:

- `semantic_artifact`
- `debug_failure`
- `candidate_window`
- `raw_payload`
- `packet_owned_symbols`
- `learning_accepted`
- `decode_success`

Avoid overloaded or context-free names:

- `data`
- `info`
- `result`
- `payload`
- `item`
- `thing`

These generic names are acceptable only in very small scopes where the meaning
is obvious.

Names should distinguish layers. For example, `symbol_stream` is not enough if
there are raw, candidate-local, and semantic symbol streams. Use names such as
`raw_symbol_stream`, `candidate_symbol_stream`, and
`semantic_symbol_stream`.

## Data Contracts

When data crosses a module boundary, its contract should be explicit.

Python options:

- `dataclass`
- `TypedDict`
- Pydantic model
- enum
- protocol
- plain class with documented invariants

C++ options:

- named struct/class
- enum class
- strong type wrapper for easily confused values
- status/result type for operations that can fail

For artifact-like data, include provenance fields when relevant:

- source path or source subsystem
- generation stage
- artifact class
- semantic comparability
- decode/validation status
- ownership guarantee
- allowed and prohibited uses

## Migration And Backward Compatibility

Post-release changes must treat existing installations as data that belongs to
the user. Any change that touches persisted configuration, options, entities,
service schemas, controller contracts, learned profiles, diagnostics, or
firmware/API payloads must include an explicit compatibility decision.

Default policy:

- Preserve existing config entries and options.
- Preserve learned fireplace profiles and serial/C/D identity data.
- Preserve entity IDs, unique IDs, device identifiers, and service schemas
  unless a migration handles the old shape.
- Preserve ESPHome firmware contracts across compatible releases, or reject
  incompatible firmware with a clear diagnostic instead of failing silently.
- Preserve existing controller behavior unless the change is opt-in or
  migrated.
- Add migration code when persisted data shape changes.
- Add tests for migrations and compatibility behavior.
- Document user-visible behavior changes in release notes.

Breaking changes are allowed only when all of these are true:

- The major version changes.
- The release notes explicitly identify the breaking change.
- The documentation explains how to upgrade safely.
- The code fails safely for unsupported old state instead of corrupting or
  silently discarding user data.

For Home Assistant config entries, prefer additive options and explicit
`async_migrate_entry` handling over destructive rewrites. For ESPHome/LilyGO
changes, prefer protocol-version checks, clear diagnostics, and documented
firmware upgrade ordering.

## Lifecycle Automation

Lifecycle operations are changes to repository state, release state, or
distribution metadata rather than normal feature implementation. Examples
include:

- stamping integration versions
- creating release tags
- publishing GitHub releases
- validating release metadata
- preparing HACS publication
- running HACS and hassfest checks
- validating ESPHome example/package builds
- publishing GitHub Pages or other generated distribution artifacts

Default policy:

- Before performing a lifecycle operation manually, check whether a matching
  GitHub Action or checked-in script already exists.
- If an action exists, use it as the canonical path. For example, version
  stamping must use the release-version workflow rather than directly editing
  `manifest.json` and `version.py`.
- If no action exists and the lifecycle operation is likely to be repeated,
  first consider whether a new workflow or script should be created to codify
  the operation.
- Prefer automation that validates before mutating repository state. If a
  workflow must mutate a branch, document that the branch must be pulled before
  further local work.
- Do not create release tags until the stamped version files, branch state, and
  validation checks are consistent.
- Release validation should verify tag format, prerelease/final release status,
  and stamped version consistency.

Manual lifecycle work is allowed when automation is unavailable or would create
more risk than it removes, but the decision should be explicit. Do not
silently bypass an existing workflow for convenience.

## Documentation Philosophy

Documentation should explain intent, constraints, provenance, invariants, and
risk. It should not narrate syntax.

The reader can see what the code does mechanically. The documentation should
explain what the code is trying to accomplish and why the shape of the code is
necessary.

Good documentation remains useful even if the implementation has a bug,
because it describes intended behavior.

Bad documentation merely repeats the implementation and can become misleading
as soon as the code changes.

## Docstrings and Public Comments

### Python

Use docstrings for public modules, classes, functions, and methods.

A Python docstring should usually include:

- one-line summary of the semantic purpose
- important assumptions or invariants
- argument meaning when not obvious from type hints
- return meaning
- raised exceptions or rejection behavior
- side effects
- lifecycle or ownership notes when relevant

Do not use docstrings to repeat the function signature. Type hints already
carry type information; docstrings should carry semantic meaning.

Private helpers do not need full docstrings unless they encode non-obvious
policy. For private helpers, a short comment after the `def` line or above a
complex block is often better.

### C++

Use documentation comments for public headers and public interfaces.

A public C++ header should explain:

- what the type/function is for
- ownership and lifetime expectations
- preconditions and postconditions
- error/status behavior
- thread-safety or reentrancy constraints
- whether inputs are borrowed, copied, retained, or mutated

Put public API documentation in headers. Do not duplicate the same
documentation in implementation files. Implementation files may add comments
that explain local algorithm choices or non-obvious constraints.

Prefer `//` for normal comments and Doxygen-style `///` for public API
documentation where generated docs are useful.

## Inline Comment Standard

Inline and block comments should answer at least one of these questions:

- Why is this code necessary?
- What invariant is being protected?
- What external constraint shaped this code?
- What policy is being enforced?
- What bug or ambiguity would a future maintainer otherwise reintroduce?
- What tradeoff was chosen?
- What larger workflow does this section support?

Inline comments should not describe obvious mechanics.

Bad:

```python
# Increment the retry count.
retry_count += 1
```

Good:

```python
# Count failed attempts separately from semantic artifacts so diagnostics can
# audit receiver quality without treating discarded packets as evidence.
retry_count += 1
```

Bad:

```python
# Build the semantic artifact.
semantic_artifact = build_semantic_artifact(candidate)
```

Good:

```python
# Packet ownership starts only after decoder selection. Raw receive buffers can
# contain noise, partial repeats, or unrelated symbols, so they remain debug-only.
semantic_artifact = build_semantic_artifact(candidate)
```

Bad:

```cpp
// Add result to failed_attempts.
failed_attempts.push_back(result);
```

Good:

```cpp
// Learning discards failed receive windows. Keep them for auditability, but do
// not expose them through the semantic comparison path.
failed_attempts.push_back(result);
```

## Comment Placement

Use comments before a block when explaining a section's purpose.

Use end-of-line comments sparingly, mostly for:

- named boolean or sentinel arguments
- compact edge-case explanations
- units or protocol-specific constants

Examples:

```cpp
reader.Read(/*allow_partial=*/false);
```

```python
timeout_s = 1.0  # Match one learning receive attempt, not the full prompt.
```

Avoid large comments inside already dense code. If a comment needs multiple
paragraphs, consider extracting the code into a named helper and documenting the
helper's contract.

## Required Documentation For Risky Code

Add explicit documentation when code involves:

- RF/protocol assumptions
- packet, frame, or artifact ownership
- retry/timeout behavior
- semantic vs debug data
- lossy normalization
- heuristic ranking
- hardware behavior
- concurrency
- caching
- persistence formats
- backward compatibility
- security-sensitive behavior
- performance-sensitive shortcuts

For this repository, artifact-producing code must clearly identify whether the
artifact is semantic evidence, candidate/debug data, raw capture data, or
heuristic output.

## Tests and Documentation

Tests should verify documented behavior.

When adding or changing documented policy, add or update tests for:

- normal success path
- rejection/failure path
- boundary conditions
- provenance or artifact classification
- backwards compatibility if applicable

If a comment says failed artifacts must never be used semantically, a test
should make that policy difficult to regress.

## Tooling Expectations

Use automated tools where possible.

Python:

- formatter: `black` or the project's established formatter
- lint: `ruff` or existing lint setup
- types: `mypy` or `pyright` where practical
- tests: `pytest`
- hooks: `pre-commit` when configured

C++:

- formatter: `clang-format`
- static analysis: `clang-tidy` where practical
- build warnings: keep warnings clean
- tests: project test framework
- runtime checks: sanitizers where practical
- docs: Doxygen-style comments for public APIs when useful

Tooling should support readability, not replace engineering judgment.

## Review Checklist

Before considering code ready:

- The main workflow is easy to find.
- Each file has a coherent responsibility.
- Important concepts have one canonical name.
- Public APIs can be used without reading implementation internals.
- Data contracts are explicit at module boundaries.
- Debug artifacts cannot be confused with semantic artifacts.
- Comments explain intent, constraints, or risk.
- Comments do not parrot code.
- Failure and rejection behavior is documented and tested.
- Tests cover success, failure, and edge cases.
- Formatting/linting has been run where applicable.

## Highest-Value Rule

If a future maintainer could plausibly misunderstand what kind of data they are
holding, whether it is trustworthy, or what policy has accepted it, the code
needs a better name, a stronger type, a clearer boundary, or a comment that
explains the intended semantics.
