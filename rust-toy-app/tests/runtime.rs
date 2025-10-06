//! Test utilities that detect container runtime availability using the action's
//! Python helpers. The probe first attempts to invoke `uv run` with the action
//! sources, falling back to the system Python interpreter when `uv` is
//! unavailable, mirroring the runtime detection logic used in production.

use std::env;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::Command;

#[cfg(test)]
use std::sync::{Mutex, OnceLock};

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("workspace root")
        .to_path_buf()
}

fn action_src_dir() -> PathBuf {
    repo_root().join(".github/actions/rust-build-release/src")
}

fn uv_binary() -> OsString {
    #[cfg(test)]
    if let Some(override_bin) = uv_binary_override() {
        return override_bin;
    }

    OsString::from("uv")
}

fn runtime_module_dir() -> PathBuf {
    #[cfg(test)]
    if let Some(dir) = runtime_dir_override() {
        return dir;
    }

    action_src_dir()
}

fn python_interpreter() -> OsString {
    env::var_os("PYTHON").unwrap_or_else(|| OsString::from("python3"))
}

fn run_with_uv(script: &str, runtime: &str, module_dir: &Path) -> Option<bool> {
    let action_dir = module_dir.parent().unwrap_or(module_dir);
    let status = Command::new(uv_binary())
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
        .env("GITHUB_ACTION_PATH", action_dir)
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
    let action_dir = module_dir.parent().unwrap_or(module_dir);
    match Command::new(python_interpreter())
        .arg("-c")
        .arg(script)
        .arg(runtime)
        .arg(module_dir)
        .env("GITHUB_ACTION_PATH", action_dir)
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
    let module_dir = runtime_module_dir();
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

#[cfg(test)]
fn runtime_dir_override() -> Option<PathBuf> {
    runtime_dir_override_storage().lock().unwrap().clone()
}

#[cfg(test)]
fn uv_binary_override() -> Option<OsString> {
    uv_binary_override_storage().lock().unwrap().clone()
}

#[cfg(test)]
fn runtime_dir_override_storage() -> &'static Mutex<Option<PathBuf>> {
    static STORAGE: OnceLock<Mutex<Option<PathBuf>>> = OnceLock::new();
    STORAGE.get_or_init(|| Mutex::new(None))
}

#[cfg(test)]
fn uv_binary_override_storage() -> &'static Mutex<Option<OsString>> {
    static STORAGE: OnceLock<Mutex<Option<OsString>>> = OnceLock::new();
    STORAGE.get_or_init(|| Mutex::new(None))
}

#[cfg(test)]
fn replace_runtime_dir_override(path: Option<PathBuf>) -> Option<PathBuf> {
    let mut guard = runtime_dir_override_storage().lock().unwrap();
    std::mem::replace(&mut *guard, path)
}

#[cfg(test)]
fn replace_uv_binary_override(bin: Option<OsString>) -> Option<OsString> {
    let mut guard = uv_binary_override_storage().lock().unwrap();
    std::mem::replace(&mut *guard, bin)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::mem;
    use std::os::unix::fs::PermissionsExt;
    use std::path::Path;
    use std::sync::{Mutex, OnceLock};

    use tempfile::tempdir;

    static OVERRIDE_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

    fn override_lock() -> &'static Mutex<()> {
        OVERRIDE_LOCK.get_or_init(|| Mutex::new(()))
    }

    struct RuntimeDirGuard(Option<PathBuf>);

    impl RuntimeDirGuard {
        fn set(path: &Path) -> Self {
            let previous = replace_runtime_dir_override(Some(path.to_path_buf()));
            Self(previous)
        }
    }

    impl Drop for RuntimeDirGuard {
        fn drop(&mut self) {
            let previous = mem::take(&mut self.0);
            replace_runtime_dir_override(previous);
        }
    }

    struct UvBinaryGuard(Option<OsString>);

    impl UvBinaryGuard {
        fn set<S: Into<OsString>>(value: S) -> Self {
            let previous = replace_uv_binary_override(Some(value.into()));
            Self(previous)
        }
    }

    impl Drop for UvBinaryGuard {
        fn drop(&mut self) {
            let previous = mem::take(&mut self.0);
            replace_uv_binary_override(previous);
        }
    }

    fn create_runtime_module(dir: &Path, body: &str) {
        fs::create_dir_all(dir).unwrap();
        fs::write(dir.join("runtime.py"), body).unwrap();
    }

    #[test]
    fn runtime_available_valid_runtime() {
        let _lock = override_lock().lock().unwrap();
        let temp_dir = tempdir().unwrap();
        create_runtime_module(
            temp_dir.path(),
            "def runtime_available(name):\n    return name == 'python3'\n",
        );
        let _module_guard = RuntimeDirGuard::set(temp_dir.path());
        let _uv_guard = UvBinaryGuard::set("nonexistent-uv");

        assert!(runtime_available("python3"));
    }

    #[test]
    fn runtime_available_invalid_runtime() {
        let _lock = override_lock().lock().unwrap();
        let temp_dir = tempdir().unwrap();
        create_runtime_module(
            temp_dir.path(),
            "def runtime_available(name):\n    return name == 'python3'\n",
        );
        let _module_guard = RuntimeDirGuard::set(temp_dir.path());
        let _uv_guard = UvBinaryGuard::set("nonexistent-uv");

        assert!(!runtime_available("foobar"));
    }

    #[test]
    fn runtime_available_missing_module_dir() {
        let _lock = override_lock().lock().unwrap();
        let temp_dir = tempdir().unwrap();
        let missing_dir = temp_dir.path().join("missing");
        let _module_guard = RuntimeDirGuard::set(&missing_dir);
        let _uv_guard = UvBinaryGuard::set("nonexistent-uv");

        assert!(!runtime_available("python3"));
    }

    #[test]
    fn run_with_uv_missing_binary_returns_none() {
        let _lock = override_lock().lock().unwrap();
        let temp_dir = tempdir().unwrap();
        let _uv_guard = UvBinaryGuard::set("surely-missing-uv");

        let result = run_with_uv("print('hello')", "python3", temp_dir.path());
        assert!(result.is_none());
    }

    #[test]
    fn run_with_uv_permission_denied_returns_none() {
        let _lock = override_lock().lock().unwrap();
        let temp_dir = tempdir().unwrap();
        let fake_uv = temp_dir.path().join("uv");
        fs::write(&fake_uv, b"#!/bin/sh\necho hi\n").unwrap();
        let mut perms = fs::metadata(&fake_uv).unwrap().permissions();
        perms.set_mode(0o644);
        fs::set_permissions(&fake_uv, perms).unwrap();
        let _uv_guard = UvBinaryGuard::set(fake_uv.as_os_str());

        let result = run_with_uv("print('hello')", "python3", temp_dir.path());
        assert!(result.is_none());
    }
}
