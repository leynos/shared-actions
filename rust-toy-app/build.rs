use std::env;
use std::fs::File;
use std::io::Write;
use std::path::PathBuf;

include!("src/lib.rs");

fn main() {
    let out_dir = PathBuf::from(env::var_os("OUT_DIR").unwrap());
    let cmd = cli();
    let man = clap_mangen::Man::new(cmd);
    let mut buffer = Vec::new();
    man.render(&mut buffer).unwrap();
    let mut out = File::create(out_dir.join("rust-toy-app.1")).unwrap();
    out.write_all(&buffer).unwrap();
}
