"""Unit tests for the mutation change-detection helper script."""

from __future__ import annotations

import json
import typing as typ

import pytest
from plumbum import local

from workflow_scripts import mutation_detect_changes as detect

if typ.TYPE_CHECKING:
    from pathlib import Path

OLD_COMMIT_DATE = "2000-01-01T00:00:00 +0000"


def _git(repo: Path, *arguments: str, commit_date: str | None = None) -> None:
    """Run git in ``repo`` with a deterministic identity."""
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    if commit_date is not None:
        env["GIT_AUTHOR_DATE"] = commit_date
        env["GIT_COMMITTER_DATE"] = commit_date
    with local.env(**env):
        local["git"]["-C", str(repo), *arguments]()


def _commit_file(
    repo: Path,
    name: str,
    *,
    commit_date: str | None = None,
    content: str = "fn main() {}\n",
) -> None:
    """Create ``name`` in ``repo`` and commit it."""
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", f"add {name}", commit_date=commit_date)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Initialize a git repository with one old commit on ``main``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # mutmut's mutation trampoline resolves the configured source_paths
    # strictly against the current working directory on every hit; tests
    # that chdir into this repository need the directory to exist or the
    # mutation run's baseline fails with FileNotFoundError.
    (repo / "workflow_scripts").mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit_file(repo, "README.md", commit_date=OLD_COMMIT_DATE, content="hi\n")
    return repo


def _config(**overrides: object) -> detect.DetectionConfig:
    """Build a DetectionConfig with test-friendly defaults."""
    values: dict[str, typ.Any] = {"base_ref": "HEAD"}
    values.update(overrides)
    return detect.DetectionConfig(**values)


class TestChangedFiles:
    """Behaviour of the git-log window scan."""

    def test_recent_commit_is_detected(self, git_repo: Path) -> None:
        """A file committed now falls inside the window."""
        _commit_file(git_repo, "src/lib.rs")
        found = detect.changed_files(_config(), repo_root=git_repo)
        assert found == ("src/lib.rs",)

    def test_old_commit_is_outside_window(self, git_repo: Path) -> None:
        """A commit with an old commit timestamp is ignored."""
        _commit_file(git_repo, "src/lib.rs", commit_date=OLD_COMMIT_DATE)
        found = detect.changed_files(_config(), repo_root=git_repo)
        assert found == ()

    def test_deleted_file_is_filtered(self, git_repo: Path) -> None:
        """A file changed then deleted within the window is skipped."""
        _commit_file(git_repo, "src/gone.rs")
        _git(git_repo, "rm", "-q", "src/gone.rs")
        _git(git_repo, "commit", "-m", "remove")
        found = detect.changed_files(_config(), repo_root=git_repo)
        assert found == ()

    def test_pathspec_filters_extension(self, git_repo: Path) -> None:
        """Only files matching the pathspec are reported."""
        _commit_file(git_repo, "src/lib.rs")
        _commit_file(git_repo, "src/notes.txt", content="notes\n")
        found = detect.changed_files(_config(), repo_root=git_repo)
        assert found == ("src/lib.rs",)


class TestBucketFiles:
    """Grouping of changed files into mutation targets."""

    def test_root_prefixes_bucket_to_root(self) -> None:
        """Files under root prefixes map to the ``.`` target."""
        config = _config()
        buckets = detect.bucket_files(
            ["src/a.rs", "examples/b.rs", "benches/c.rs", "docs/d.rs"], config
        )
        assert buckets == {".": ["src/a.rs", "examples/b.rs", "benches/c.rs"]}

    def test_extra_crate_takes_precedence(self) -> None:
        """Extra-crate files map to their crate, not the root target."""
        config = _config(extra_crate_dirs=("testkit",))
        buckets = detect.bucket_files(
            ["src/a.rs", "testkit/src/lib.rs", "testkit/build.rs"], config
        )
        assert buckets == {".": ["src/a.rs"], "testkit": ["testkit/src/lib.rs"]}


class TestMatrices:
    """Matrix construction for full and scoped runs."""

    def test_full_run_shards_root_only(self) -> None:
        """Full runs fan the root target out and keep extras single-shard."""
        config = _config(shard_count=3, extra_crate_dirs=("testkit",))
        entries = detect.full_run_matrix(config)
        root = [e for e in entries if e.dir == "."]
        extra = [e for e in entries if e.dir == "testkit"]
        assert [e.shard for e in root] == [0, 1, 2]
        assert all(e.shard_count == 3 and e.files == "" for e in root)
        assert len(extra) == 1
        assert extra[0].shard_count == 1
        assert extra[0].slug == "testkit"
        assert extra[0].files == "", "extra crates run unscoped (empty files)"
        assert extra[0].shard == 0, "single-shard extras use shard index 0"

    def test_scoped_run_strips_extra_dir_prefix(self) -> None:
        """Scoped entries carry files relative to their target directory."""
        config = _config(extra_crate_dirs=("testkit",))
        buckets = {
            "testkit": ["testkit/src/lib.rs"],
            ".": ["src/a.rs", "src/b.rs"],
        }
        entries = detect.scoped_run_matrix(buckets, config)
        assert [e.slug for e in entries] == ["root", "testkit"]
        assert [e.dir for e in entries] == [".", "testkit"], (
            "each entry should carry its own target directory"
        )
        assert entries[0].files == "src/a.rs src/b.rs"
        assert entries[1].files == "src/lib.rs"
        assert all(e.shard == 0 and e.shard_count == 1 for e in entries)

    def test_matrix_json_shape(self) -> None:
        """The matrix output parses back into an ``include`` list."""
        entries = detect.full_run_matrix(_config(shard_count=2))
        payload = json.loads(detect.matrix_json(entries))
        assert list(payload) == ["include"]
        assert payload["include"][0]["dir"] == "."
        assert payload["include"][0]["slug"] == "root"
        first = payload["include"][0]
        assert list(first) == sorted(first), (
            "matrix entry keys should be serialized in sorted order"
        )


class TestMainEntry:
    """End-to-end behaviour of the CLI entry point."""

    def _run_main(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        event_name: str,
        repo: Path,
    ) -> dict[str, str]:
        """Invoke ``main`` via the app and parse the step outputs."""
        output_file = tmp_path / "github_output"
        output_file.touch()
        summary_file = tmp_path / "github_summary"
        summary_file.touch()
        monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
        monkeypatch.setenv("INPUT_EVENT_NAME", event_name)
        monkeypatch.setenv("INPUT_SHARD_COUNT", "2")
        monkeypatch.setenv("INPUT_BASE_REF", "HEAD")
        monkeypatch.chdir(repo)
        detect.app([])
        outputs: dict[str, str] = {}
        for line in output_file.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            outputs[key] = value
        return outputs

    def test_dispatch_bypasses_detection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_repo: Path
    ) -> None:
        """Dispatch runs emit a sharded full matrix without consulting git."""
        outputs = self._run_main(
            tmp_path, monkeypatch, event_name="workflow_dispatch", repo=git_repo
        )
        assert outputs["has_changes"] == "true"
        matrix = json.loads(outputs["matrix"])
        assert [entry["shard"] for entry in matrix["include"]] == [0, 1]
        assert outputs["root_files"] == ""

    def test_schedule_without_changes_skips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_repo: Path
    ) -> None:
        """Scheduled runs with no recent changes emit the skip outputs."""
        outputs = self._run_main(
            tmp_path, monkeypatch, event_name="schedule", repo=git_repo
        )
        assert outputs["has_changes"] == "false"
        assert json.loads(outputs["matrix"]) == {"include": []}
        summary = (tmp_path / "github_summary").read_text(encoding="utf-8")
        assert "Mutation testing skipped" in summary
        assert "`HEAD`" in summary, "the skip message should name the base ref"
        assert "25 hours" in summary, "the skip message should name the window"

    def test_schedule_with_changes_scopes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_repo: Path
    ) -> None:
        """Scheduled runs with recent changes emit a scoped single shard."""
        _commit_file(git_repo, "src/lib.rs")
        outputs = self._run_main(
            tmp_path, monkeypatch, event_name="schedule", repo=git_repo
        )
        assert outputs["has_changes"] == "true"
        matrix = json.loads(outputs["matrix"])
        assert len(matrix["include"]) == 1
        entry = matrix["include"][0]
        assert entry["files"] == "src/lib.rs"
        assert entry["shard_count"] == 1
        assert outputs["root_files"] == "src/lib.rs"

    def test_missing_github_output_fails(
        self, monkeypatch: pytest.MonkeyPatch, git_repo: Path
    ) -> None:
        """A missing GITHUB_OUTPUT is a hard error."""
        monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
        monkeypatch.setenv("INPUT_EVENT_NAME", "schedule")
        monkeypatch.chdir(git_repo)
        with pytest.raises(SystemExit) as excinfo:
            detect.app([])
        assert excinfo.value.code == 1


def test_split_csv_trims_and_drops_empties() -> None:
    """CSV inputs tolerate whitespace and empty segments."""
    assert detect.split_csv(" a/, ,b/,") == ("a/", "b/")
    assert detect.split_csv("") == ()
