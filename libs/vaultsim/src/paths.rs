use std::path::PathBuf;

/// Resolves the pack's `config/the_vault/gen/1.0` folder: `WV_GEN_ROOT` if set, otherwise the
/// checkout that `setup/fetch_sources.py` writes under the repo's `cache/`. Prints an error when
/// the folder is missing so a silent empty simulation never happens.
pub fn gen_root() -> String {
    let p = match std::env::var("WV_GEN_ROOT") {
        Ok(v) if !v.is_empty() => PathBuf::from(v),
        _ => repo_root().join("cache/pack/config/the_vault/gen/1.0"),
    };
    if !p.is_dir() {
        eprintln!("[paths] ERROR: gen root {} does not exist; run `python setup/fetch_sources.py` or set WV_GEN_ROOT", p.display());
    }
    p.to_string_lossy().into_owned()
}

/// Repo-relative output path under `out/vaultsim/`, created on demand.
pub fn out_path(name: &str) -> String {
    let dir = repo_root().join("out/vaultsim");
    if let Err(e) = std::fs::create_dir_all(&dir) {
        eprintln!("[paths] ERROR: cannot create {}: {e}", dir.display());
    }
    dir.join(name).to_string_lossy().into_owned()
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}
