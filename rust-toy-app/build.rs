use std::env;

use std::path::PathBuf;
use time::OffsetDateTime;

#[allow(dead_code)]
#[path = "src/cli.rs"]
mod cli;

fn main() -> std::io::Result<()> {
    // Rebuild when the CLI definition changes.
    println!("cargo:rerun-if-changed=src/cli.rs");
    println!("cargo:rerun-if-changed=Cargo.toml");
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-env-changed=SOURCE_DATE_EPOCH");
    let out_dir = env::var("OUT_DIR").map(PathBuf::from).map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
    let cmd = cli::command();
    let man = clap_mangen::Man::new(cmd).date(build_date());
    let mut buffer = Vec::new();
    man.render(&mut buffer)?;
    std::fs::write(out_dir.join("rust-toy-app.1"), &buffer)?;
    Ok(())
}

fn build_date() -> String {
    env::var("SOURCE_DATE_EPOCH")
        .ok()
        .and_then(|s| s.parse::<i64>().ok())
        .and_then(|e| OffsetDateTime::from_unix_timestamp(e).ok())
        .map(|dt| dt.date().to_string())
        .unwrap_or_else(|| "1970-01-01".to_string())
}


