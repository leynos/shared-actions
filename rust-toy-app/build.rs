use std::env;

use std::path::PathBuf;

#[path = "src/cli.rs"]
mod cli;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Rebuild when the CLI definition changes.
    println!("cargo:rerun-if-changed=src/cli.rs");
    let out_dir = PathBuf::from(env::var("OUT_DIR")?);
    let cmd = cli::command();
    let man = clap_mangen::Man::new(cmd);
    let mut buffer = Vec::new();
    man.render(&mut buffer)?;
    std::fs::write(out_dir.join("rust-toy-app.1"), &buffer)?;
    Ok(())
}
