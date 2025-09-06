use std::env;
use std::fs::File;
use std::io::Write;
use std::path::PathBuf;

#[path = "src/cli.rs"]
mod cli;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let out_dir = PathBuf::from(env::var("OUT_DIR")?);
    let cmd = cli::command();
    let man = clap_mangen::Man::new(cmd);
    let mut buffer = Vec::new();
    man.render(&mut buffer)?;
    let mut out = File::create(out_dir.join("rust-toy-app.1"))?;
    out.write_all(&buffer)?;
    Ok(())
}
