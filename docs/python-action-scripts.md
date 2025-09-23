# Python Action Scripts

Some actions rely on small Python helpers to keep the composite YAML focused on
orchestration. These scripts live in an action's `scripts/` directory and are
executed with [`uv`](https://github.com/astral-sh/uv). Each script declares its
own runtime requirements with a [PEP 723](https://peps.python.org/pep-0723/)
header so the runner can install dependencies on demand.

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "cyclopts>=2.9,<3.0",
#   "plumbum>=1.8,<2.0",
#   "pyyaml>=6.0,<7.0",
#   "typer>=0.9,<1.0",
#   # optional: coloured error reporting via script_utils
# ]
# ///
```

Command-line parsing is handled by
[Cyclopts](https://github.com/davidhewitt/cyclopts), which reads GitHub Action
inputs directly from `INPUT_*` environment variables. Mapping the environment is
as simple as configuring the application with the shared prefix:

```python
import cyclopts
from cyclopts import App

app = App()
_env_config = cyclopts.config.Env("INPUT_", command=False)
app.config = (*tuple(getattr(app, "config", ())), _env_config)


@app.default
def main(*, bin_name: str, version: str, formats: list[str] | None = None) -> None:
    ...


if __name__ == "__main__":
    app()
```

Cyclopts automatically splits list inputs on whitespace and honours required
parameters, so the scripts remain declarative and free of ad-hoc parsing logic.
The [plumbum](https://plumbum.readthedocs.io/) library is used for invoking
external commands through the shared `run_cmd` helper, which prints each command
before execution to aid debugging.
