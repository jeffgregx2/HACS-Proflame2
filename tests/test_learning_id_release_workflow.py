"""Keep helper publication additive and tied to validated release source."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_helper_release_assets_follow_validation_without_clobbering():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release-validation.yml").read_text())
    jobs = workflow["jobs"]
    validate = jobs["validate-release"]
    assert any("validate_release_source.py" in step.get("run", "") for step in validate["steps"])
    assert validate["outputs"]["source_commit"] == "${{ steps.source.outputs.commit }}"
    upload_job = jobs["learning-id-assets"]
    assert upload_job["needs"] == "validate-release"
    assert upload_job["if"] == "github.event_name == 'release' && github.event.action == 'published'"
    steps = upload_job["steps"]
    assert steps[0]["with"]["ref"] == "${{ needs.validate-release.outputs.source_commit }}"
    build, test, upload = [step for step in steps if step.get("name", "").startswith(("Build", "Test", "Add helper"))]
    assert "--require-clean" in build["run"]
    assert "learning-id-package-test" in test["run"]
    assert "gh release upload" in upload["run"]
    assert "--clobber" not in upload["run"]
    assert "delete" not in upload["run"]
    assert "*" not in upload["run"]
    assert '"$OUTPUT_DIR/proflame2-learning-id.pyz"' in upload["run"]
    assert '"$OUTPUT_DIR/proflame2-learning-id.pyz.sha256"' in upload["run"]
    assert steps[-1]["if"] == "always()"
    assert "runner.temp" not in str(upload_job.get("env", {}))
    assert any('mktemp -d "$RUNNER_TEMP/' in step.get("run", "") for step in steps)


def test_development_packages_test_both_operating_systems_and_python_bounds():
    workflow = yaml.safe_load((ROOT / ".github/workflows/learning-id-package.yml").read_text())
    job = workflow["jobs"]["source-archive"]
    assert job["timeout-minutes"] == 12
    archive_test = next(
        step for step in job["steps"] if step.get("name") == "Test the actual archive without RF hardware"
    )
    assert archive_test["timeout-minutes"] == 10
    assert job["strategy"]["matrix"] == {"os": ["ubuntu-latest", "macos-latest"], "python": ["3.10", "3.14"]}
    assert workflow["permissions"] == {"contents": "read"}
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "make learning-id-package " in commands
    assert "make learning-id-package-test " in commands
    assert "gh release" not in commands
    assert any(step.get("uses") == "actions/upload-artifact@v4" for step in job["steps"])
    assert "runner.temp" not in str(job.get("env", {}))
