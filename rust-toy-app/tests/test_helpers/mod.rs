//! Test utilities for thread-safe environment variable mutations.
//!
//! This module provides `EnvGuard`, an RAII helper that serialises environment
//! variable changes during tests using a global mutex and restores previous
//! values when the guard is dropped.

use std::env;
use std::sync::{Mutex, MutexGuard, OnceLock};

fn env_mutex() -> &'static Mutex<()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
}

pub struct EnvGuard {
    key: String,
    previous: Option<String>,
    lock_guard: Option<MutexGuard<'static, ()>>,
}

impl EnvGuard {
    pub fn set(key: &str, value: &str) -> Self {
        let lock_guard = env_mutex().lock().unwrap();
        let previous = env::var(key).ok();

        // SAFETY: Access is serialized by the mutex, preventing concurrent
        // mutations of the process environment during this guard's lifetime.
        unsafe { env::set_var(key, value) };

        Self {
            key: key.to_owned(),
            previous,
            lock_guard: Some(lock_guard),
        }
    }
}

impl Drop for EnvGuard {
    fn drop(&mut self) {
        match self.previous.as_ref() {
            Some(previous) => unsafe { env::set_var(&self.key, previous) },
            None => unsafe { env::remove_var(&self.key) },
        }
        // Release the mutex guard after restoring the environment variable.
        self.lock_guard.take();
    }
}
