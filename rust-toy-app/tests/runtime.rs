use std::env;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::Command;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().expect("workspace root").to_path_buf()
}

fn action_src_dir() -> PathBuf {
    repo_root().join(".github/actions/rust-build-release/src")
}

fn python_interpreter() -> OsString {
    env::var_os("PYTHON").unwrap_or_else(|| OsString::from("python3"))
}

fn run_with_uv(script: &str, runtime: &str, module_dir: &Path) -> Option<bool> {
    let status = Command::new("uv")
        .arg("run")
        .arg("--with")
        .arg("typer")
        .arg("--with")
        .arg("plumbum")
        .arg("python")
        .arg("-c")
        .arg(script)
        .arg(runtime)
        .arg(module_dir)
        .status();

    match status {
        Ok(status) => Some(status.success()),
        Err(err) => {
            eprintln!("failed to execute uv run: {err}");
            None
        }
    }
}

fn run_with_python(script: &str, runtime: &str, module_dir: &Path) -> bool {
    match Command::new(python_interpreter())
        .arg("-c")
        .arg(script)
        .arg(runtime)
        .arg(module_dir)
        .status()
    {
        Ok(status) => status.success(),
        Err(err) => {
            eprintln!("failed to execute python runtime probe: {err}");
            false
        }
    }
}

pub fn runtime_available(name: &str) -> bool {
    let module_dir = action_src_dir();
    let script = r#"
import pathlib
import sys

runtime_name = sys.argv[1]
module_dir = pathlib.Path(sys.argv[2])
if not module_dir.exists():
    sys.exit(1)
sys.path.insert(0, str(module_dir))
from runtime import runtime_available as _runtime_available
sys.exit(0 if _runtime_available(runtime_name) else 1)
"#;

    if let Some(result) = run_with_uv(script, name, &module_dir) {
        return result;
    }

    run_with_python(script, name, &module_dir)
}
