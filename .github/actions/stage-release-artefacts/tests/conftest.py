"""Shared pytest fixtures for stage-release-artefacts tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from syspath_hack import prepend_to_syspath

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def pytest_configure(config: pytest.Config) -> None:
    """Set up sys.path and re-export stage_common symbols before collection.

    pytest invokes this hook after importing conftest.py but before
    collecting and importing peer test modules. Both this conftest and
    sibling test modules import ``stage_common`` at module top level, so
    the scripts directory must be on ``sys.path`` before that collection
    pass begins.
    """
    del config
    prepend_to_syspath(SCRIPTS_DIR)
    global ArtefactConfig, BinstallConfig, StagingConfig
    global StageError, StageResult, stage_artefacts
    from stage_common import StageError
    from stage_common.config import (
        ArtefactConfig,
        BinstallConfig,
        StagingConfig,
    )
    from stage_common.pipeline import StageResult, stage_artefacts


# Restricted to the Basic Multilingual Plane (max_codepoint=0xFFFF) to avoid
# macOS EILSEQ on supplementary characters. Excludes surrogates (Cs), C0/C1
# control characters (Cc), unassigned codepoints (Cn), Windows-illegal filename
# characters, and POSIX path separators.
PS_MODULE_NAMES = st.text(
    alphabet=st.characters(
        blacklist_characters='/\\\x00:*?"<>|',
        blacklist_categories=("Cs", "Cc", "Cn"),
        max_codepoint=0xFFFF,
    ),
    min_size=1,
    max_size=12,
).filter(lambda value: value not in {".", ".."})
TEMPLATE_SAFE_PS_MODULE_NAMES = PS_MODULE_NAMES.filter(
    lambda value: "{" not in value and "}" not in value
)
PATH_SEGMENTS = st.text(
    alphabet=st.characters(
        blacklist_characters='/\\\x00:*?"<>|',
        blacklist_categories=("Cs", "Cc", "Cn"),
        max_codepoint=0xFFFF,
    ),
    min_size=1,
    max_size=8,
).filter(lambda value: value not in {".", ".."})
TEMPLATE_SAFE_PATH_SEGMENTS = PATH_SEGMENTS.filter(
    lambda value: "{" not in value and "}" not in value
)
TRAVERSAL_DESTINATIONS = st.text(
    alphabet=st.characters(
        blacklist_characters='/\\\x00:*?"<>|',
        blacklist_categories=("Cs", "Cc", "Cn"),
        max_codepoint=0xFFFF,
    ),
    min_size=1,
    max_size=12,
).filter(lambda value: "{" not in value and "}" not in value)
HYPOTHESIS_SETTINGS = settings(
    max_examples=25,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
ARCHIVE_MEMBER_NAMES = st.text(
    alphabet=st.characters(
        blacklist_characters="/\\\0",
        blacklist_categories=("Cs",),
    ),
    min_size=1,
    max_size=40,
).filter(lambda value: value not in {".", ".."})


def make_linux_config(
    workspace: Path,
    artefacts: list[ArtefactConfig],
) -> StagingConfig:
    """Return a minimal Linux StagingConfig for use in tests."""
    return StagingConfig(
        workspace=workspace,
        bin_name="myapp",
        dist_dir="dist",
        checksum_algorithm="sha256",
        artefacts=artefacts,
        platform="linux",
        arch="x86_64",
        target="x86_64-unknown-linux-gnu",
    )


def make_powershell_workspace(
    tmp_path: Path,
    target: str = "x86_64-pc-windows-msvc",
) -> tuple[Path, Path]:
    """Create a workspace containing MyTool PowerShell sidecar source files."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source_dir = (
        workspace
        / "target"
        / "orthohelp"
        / target
        / "release"
        / "powershell"
        / "MyTool"
    )
    help_dir = source_dir / "en-US"
    help_dir.mkdir(parents=True)
    for f in [
        source_dir / "MyTool.psm1",
        source_dir / "MyTool.psd1",
        help_dir / "MyTool-help.xml",
        help_dir / "about_MyTool.help.txt",
    ]:
        f.write_text(f.name, encoding="utf-8")
    return workspace, tmp_path / "output"


def powershell_artefact_configs() -> list[ArtefactConfig]:
    """Return the four optional ArtefactConfig entries for the MyTool sidecar."""
    base = "target/orthohelp/{target}/release/powershell/MyTool"
    return [
        ArtefactConfig(
            source=f"{base}/MyTool.psm1",
            destination="MyTool/MyTool.psm1",
            required=False,
        ),
        ArtefactConfig(
            source=f"{base}/MyTool.psd1",
            destination="MyTool/MyTool.psd1",
            required=False,
        ),
        ArtefactConfig(
            source=f"{base}/en-US/MyTool-help.xml",
            destination="MyTool/en-US/MyTool-help.xml",
            required=False,
        ),
        ArtefactConfig(
            source=f"{base}/en-US/about_MyTool.help.txt",
            destination="MyTool/en-US/about_MyTool.help.txt",
            required=False,
        ),
    ]


def make_windows_config(
    workspace: Path,
    artefacts: list[ArtefactConfig] | None = None,
) -> StagingConfig:
    """Return a Windows StagingConfig for PowerShell sidecar tests."""
    artefacts_to_use = (
        artefacts if artefacts is not None else powershell_artefact_configs()
    )
    return StagingConfig(
        workspace=workspace,
        bin_name="mytool",
        dist_dir="dist",
        checksum_algorithm="sha256",
        artefacts=artefacts_to_use,
        platform="windows",
        arch="x86_64",
        target="x86_64-pc-windows-msvc",
    )


def _assert_path_traversal_rejected(tmp_path: Path, destination: str) -> None:
    """Arrange a minimal workspace and assert StageError for escaping destination."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "myapp").write_text("binary content", encoding="utf-8")

    config = make_linux_config(
        workspace,
        [
            ArtefactConfig(
                source="myapp",
                destination=destination,
            )
        ],
    )

    with pytest.raises(StageError, match="escapes staging directory"):
        stage_artefacts(config)


@pytest.fixture
def bdd_context(tmp_path: Path) -> dict[str, object]:
    """Return mutable context for pytest-bdd steps."""
    return {"workspace": tmp_path / "workspace", "target": ""}
