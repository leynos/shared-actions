"""Tests for the ``determine-release-modes`` action script."""

from __future__ import annotations

import importlib.util
import json
import sys
import typing as typ
from types import ModuleType

import pytest
from syspath_hack import prepend_to_syspath

if typ.TYPE_CHECKING:
    from pathlib import Path as PathType

from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "determine_release_modes.py"
)
SCRIPT_DIR = MODULE_PATH.parent
prepend_to_syspath(SCRIPT_DIR)

spec = importlib.util.spec_from_file_location(
    "determine_release_modes_module", MODULE_PATH
)
if spec is None or spec.loader is None:  # pragma: no cover - defensive import guard
    message = "Unable to load determine_release_modes module for testing"
    raise RuntimeError(message)
module = importlib.util.module_from_spec(spec)
if not isinstance(module, ModuleType):  # pragma: no cover - importlib contract
    message = "module_from_spec did not return a ModuleType"
    raise TypeError(message)
sys.modules[spec.name] = module
spec.loader.exec_module(module)  # type: ignore[misc]
drm = module


class TestDetermineReleaseModes:
    """Tests for the determine_release_modes function."""

    def test_push_event_publishes_by_default(self) -> None:
        """Tag pushes default to publishing and uploading artifacts."""
        result = drm.determine_release_modes("push", {})

        assert result.dry_run is False
        assert result.should_publish is True
        assert result.should_upload_workflow_artifacts is True

    def test_pull_request_defaults_to_dry_run(self) -> None:
        """Pull requests default to dry-run mode."""
        result = drm.determine_release_modes("pull_request", {})

        assert result.dry_run is True
        assert result.should_publish is False
        assert result.should_upload_workflow_artifacts is False

    def test_workflow_call_respects_inputs(self) -> None:
        """Workflow calls respect the provided inputs."""
        event = {"inputs": {"dry-run": "false", "publish": "true"}}
        result = drm.determine_release_modes("workflow_call", event)

        assert result.dry_run is False
        assert result.should_publish is True
        assert result.should_upload_workflow_artifacts is True

    def test_workflow_call_defaults_to_non_dry_run(self) -> None:
        """Workflow calls default to non-dry-run when not specified."""
        result = drm.determine_release_modes("workflow_call", {})

        assert result.dry_run is False
        assert result.should_publish is False
        assert result.should_upload_workflow_artifacts is True

    def test_workflow_call_dry_run_disables_publish(self) -> None:
        """Dry-run mode disables publishing even if requested."""
        event = {"inputs": {"dry-run": "true", "publish": "true"}}
        result = drm.determine_release_modes("workflow_call", event)

        assert result.dry_run is True
        assert result.should_publish is False
        assert result.should_upload_workflow_artifacts is False

    def test_unsupported_event_raises_error(self) -> None:
        """Unsupported event types raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported event"):
            drm.determine_release_modes("schedule", {})

    def test_input_override_dry_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """INPUT_DRY_RUN environment variable overrides event inputs."""
        monkeypatch.setenv("INPUT_DRY_RUN", "true")
        event = {"inputs": {"dry-run": "false"}}

        result = drm.determine_release_modes("workflow_call", event)

        assert result.dry_run is True
        assert result.should_publish is False

    def test_input_override_publish(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """INPUT_PUBLISH environment variable overrides event inputs."""
        monkeypatch.setenv("INPUT_PUBLISH", "true")
        event = {"inputs": {"publish": "false"}}

        result = drm.determine_release_modes("workflow_call", event)

        assert result.should_publish is True

    def test_input_override_publish_on_push(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Push events always publish regardless of INPUT_PUBLISH override."""
        monkeypatch.setenv("INPUT_PUBLISH", "false")

        result = drm.determine_release_modes("push", {})

        # Push events always publish
        assert result.should_publish is True


class TestReleaseModes:
    """Tests for the ReleaseModes dataclass."""

    def test_to_output_mapping(self) -> None:
        """Output mapping serializes booleans as lowercase strings."""
        modes = drm.ReleaseModes(
            dry_run=True,
            should_publish=False,
            should_upload_workflow_artifacts=False,
        )
        mapping = modes.to_output_mapping()

        assert mapping == {
            "dry_run": "true",
            "should_publish": "false",
            "should_upload_workflow_artifacts": "false",
        }

    def test_to_output_mapping_all_true(self) -> None:
        """Output mapping handles all-true values."""
        modes = drm.ReleaseModes(
            dry_run=False,
            should_publish=True,
            should_upload_workflow_artifacts=True,
        )
        mapping = modes.to_output_mapping()

        assert mapping == {
            "dry_run": "false",
            "should_publish": "true",
            "should_upload_workflow_artifacts": "true",
        }


class TestExtractInputs:
    """Tests for the _extract_inputs helper function."""

    def test_returns_inputs_from_event(self) -> None:
        """Inputs are extracted from the event payload."""
        event = {"inputs": {"dry-run": "true", "publish": "false"}}
        result = drm._extract_inputs(event)

        assert result == {"dry-run": "true", "publish": "false"}

    def test_returns_empty_dict_for_missing_inputs(self) -> None:
        """Missing inputs key returns empty dict."""
        result = drm._extract_inputs({})

        assert result == {}

    def test_raises_for_non_mapping_inputs(self) -> None:
        """Non-mapping inputs raise ValueError."""
        with pytest.raises(ValueError, match="must be a mapping"):
            drm._extract_inputs({"inputs": "not-a-dict"})


class TestMain:
    """Tests for the main entry point."""

    def test_main_requires_github_event_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing GITHUB_EVENT_NAME raises RuntimeError."""
        monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
        monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
        monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

        with pytest.raises(RuntimeError, match="GITHUB_EVENT_NAME"):
            drm.main()

    def test_main_requires_github_event_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing GITHUB_EVENT_PATH raises RuntimeError."""
        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
        monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

        with pytest.raises(RuntimeError, match="GITHUB_EVENT_PATH"):
            drm.main()

    def test_main_requires_github_output(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: PathType
    ) -> None:
        """Missing GITHUB_OUTPUT raises RuntimeError."""
        event_file = tmp_path / "event.json"
        event_file.write_text("{}", encoding="utf-8")

        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
        monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

        with pytest.raises(RuntimeError, match="GITHUB_OUTPUT"):
            drm.main()

    def test_main_writes_outputs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: PathType,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Main function writes outputs to GITHUB_OUTPUT."""
        event_file = tmp_path / "event.json"
        event_file.write_text("{}", encoding="utf-8")
        output_file = tmp_path / "outputs"

        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
        monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

        drm.main()

        contents = output_file.read_text(encoding="utf-8").splitlines()
        assert "dry_run=false" in contents
        assert "should_publish=true" in contents
        assert "should_upload_workflow_artifacts=true" in contents

        captured = capsys.readouterr()
        assert "Release modes:" in captured.out

    def test_main_with_workflow_call_event(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: PathType,
    ) -> None:
        """Main handles workflow_call events with inputs."""
        event_file = tmp_path / "event.json"
        event_data = {"inputs": {"dry-run": "true", "publish": "false"}}
        event_file.write_text(json.dumps(event_data), encoding="utf-8")
        output_file = tmp_path / "outputs"

        monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_call")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
        monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

        drm.main()

        contents = output_file.read_text(encoding="utf-8").splitlines()
        assert "dry_run=true" in contents
        assert "should_publish=false" in contents
        assert "should_upload_workflow_artifacts=false" in contents

    def test_main_handles_missing_event_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: PathType,
    ) -> None:
        """Main handles missing event file gracefully."""
        event_file = tmp_path / "nonexistent.json"
        output_file = tmp_path / "outputs"

        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
        monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

        drm.main()

        # Should still work with empty event payload
        contents = output_file.read_text(encoding="utf-8").splitlines()
        assert "dry_run=false" in contents


class TestFormatBool:
    """Tests for the _format_bool helper function."""

    def test_formats_true(self) -> None:
        """True is formatted as lowercase 'true'."""
        assert drm._format_bool(value=True) == "true"

    def test_formats_false(self) -> None:
        """False is formatted as lowercase 'false'."""
        assert drm._format_bool(value=False) == "false"


class TestLoadEvent:
    """Tests for the _load_event helper function."""

    def test_loads_json_file(self, tmp_path: PathType) -> None:
        """JSON file is loaded correctly."""
        event_file = tmp_path / "event.json"
        event_file.write_text('{"inputs": {"key": "value"}}', encoding="utf-8")

        result = drm._load_event(event_file)

        assert result == {"inputs": {"key": "value"}}

    def test_returns_empty_dict_for_missing_file(self, tmp_path: PathType) -> None:
        """Missing file returns empty dict."""
        event_file = tmp_path / "nonexistent.json"

        result = drm._load_event(event_file)

        assert result == {}


class TestWriteOutputs:
    """Tests for the _write_outputs helper function."""

    def test_writes_outputs_to_file(self, tmp_path: PathType) -> None:
        """Outputs are written to the specified file."""
        output_file = tmp_path / "outputs"
        modes = drm.ReleaseModes(
            dry_run=True,
            should_publish=False,
            should_upload_workflow_artifacts=False,
        )

        drm._write_outputs(output_file, modes)

        contents = output_file.read_text(encoding="utf-8").splitlines()
        assert "dry_run=true" in contents
        assert "should_publish=false" in contents
        assert "should_upload_workflow_artifacts=false" in contents

    def test_creates_parent_directories(self, tmp_path: PathType) -> None:
        """Parent directories are created if needed."""
        output_file = tmp_path / "subdir" / "outputs"
        modes = drm.ReleaseModes(
            dry_run=False,
            should_publish=True,
            should_upload_workflow_artifacts=True,
        )

        drm._write_outputs(output_file, modes)

        assert output_file.exists()

    def test_appends_to_existing_file(self, tmp_path: PathType) -> None:
        """Outputs are appended to existing file."""
        output_file = tmp_path / "outputs"
        output_file.write_text("existing=value\n", encoding="utf-8")
        modes = drm.ReleaseModes(
            dry_run=True,
            should_publish=False,
            should_upload_workflow_artifacts=False,
        )

        drm._write_outputs(output_file, modes)

        contents = output_file.read_text(encoding="utf-8")
        assert "existing=value" in contents
        assert "dry_run=true" in contents
