# Learning ID Packaging

The single-file Linux/macOS distribution is a source-only Python ZIP application
(`.pyz`). It uses the same collector and shared protocol modules as the installed
helper. Python 3.10+, an SDR, and compatible rtl_433 are user prerequisites.
There is no bundled interpreter, native executable, dependency installation, or
persistent user configuration. End-user instructions, including inspecting the
source without running it, are in [Using rtl_433 for learning](Using_rtl_433_for_learning.md#download-and-inspect-the-helper).

## Local Builds

From the repository root, choose a new output directory outside the checkout:

```sh
make learning-id-package OUTPUT_DIR=/tmp/proflame2-learning-id
make learning-id-package-test ARCHIVE=/tmp/proflame2-learning-id/proflame2-learning-id.pyz
```

The first target requires only Python's standard library and builds from the
current working files, including uncommitted edits. It retains exactly two
requested outputs: `proflame2-learning-id.pyz` and its `.pyz.sha256` checksum.
Existing files are not overwritten; choose a new output directory for another
build, or deliberately remove your previous outputs. Generated packages are
not committed. Delete your output directory when finished with it.

The test target creates invocation-owned environments under `/tmp`, installs
pytest into the test environment only, and tests the actual archive using a
separate interpreter with no pip, helper, HA, or rtl_433 installed. That target
needs access to download pytest; end-user archive execution does not. Scratch
files/environments are removed on success, failure, and handled cancellation.
Forced termination or machine failure cannot guarantee cleanup.

Without `ARCHIVE`, `make learning-id-package-test` builds and tests a temporary
archive, then removes it too. Use `TEST_BOOTSTRAP_PYTHON=/path/to/python3` to
select the build/test interpreter. No shared or persistent virtual environment
is used.

## Source and Provenance

`tools/learning_id/build_archive.py` is the single builder used locally and in
CI. It allowlists runtime helper modules, packages the protocol's Python source,
and includes a readable entry point, README, license, and neutral build metadata.
It excludes tests, capture fixtures, private files, build tools, bytecode, and
machine configuration. Review the allowlist when adding a runtime module.

`proflame2_learning_id/BUILD_INFO.json` records the integration's manifest version,
Git source commit, dirty-checkout flag, and a digest of the packaged content.
Dirty builds refer to the base commit plus working-file changes, not an exact
committed snapshot. Local source exports without Git can build with unknown
provenance; release builds require known, clean provenance.

Archive members use fixed timestamps and permissions; builds from unchanged
inputs using the same tools are repeatable. The separate checksum covers the
complete archive. Neither checksum nor content digest is an authenticity
signature. No build-machine paths, usernames, or credentials are included.

The archive runs directly without extracting or installing. The entry point
also supports running after manual inspection/extraction and disables bytecode
cache creation. `--help` lists the options; `--inspect-pyz-archive` prints the
optional inspection instructions and exits without starting a receiver.
Both flags run helper code to print text; the documented ZIP inspection commands
do not execute the helper.

## GitHub Automation

- **Learning ID Package:** PRs, pushes to `dev`/`main`, and manual dispatch build
  and test with Python 3.10/3.14 on Linux/macOS. Successful jobs retain `.pyz` and
  checksum downloads as Actions artifacts for 14 days. These are development
  downloads, not GitHub releases.
- **Validate Release:** the existing source/version validation remains intact.
  On release publication, an additional job checks out the exact validated
  commit, builds with `--require-clean`, tests the archive, and uploads only the
  `.pyz` and checksum to that existing release. Editing release notes or manually
  running validation does not upload assets.

The helper upload does not create releases, move tags, edit release notes,
replace other assets, or change HA/ESPHome artifact generation. It intentionally
does not use `--clobber`: existing same-named assets cause failure rather than
silent replacement. Investigate and explicitly resolve a partial/duplicate
upload before retrying. Automatic source-code downloads remain unchanged.

CI and local builds invoke the same Make targets. Tests cover ZIP contents and
checksums, readable help, version/provenance reporting, source extraction,
legacy/extended collection, guided prompts, managed-receiver cleanup, and
argument rejection before receiver startup. No RF hardware is used by CI.
