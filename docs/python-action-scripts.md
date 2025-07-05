# Python Action Scripts

Some actions use short helper scripts for logic that would otherwise live in bash.
These scripts are stored under `scripts/` and executed with [`uv`](https://github.com/astral-sh/uv) using
`uv run --script`.  Each script declares its own dependencies via the
special comment header understood by uv.

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["plumbum", "typer"]
# ///
```

The [Typer](https://typer.tiangolo.com/) library is used for argument parsing and
error handling, while [plumbum](https://plumbum.readthedocs.io/) provides simple
command execution.  By isolating logic in Python we keep the composite action
YAML minimal and benefit from better readability and testability.
