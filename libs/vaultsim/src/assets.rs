use std::path::PathBuf;

/// Pluggable byte-loading so the same simulator logic can run against real files (native CLI,
/// `FsAssetSource`) or embedded-at-build-time data (wasm, see the wv-web crate's own
/// `EmbeddedAssetSource`) without `DataSource`/`Structure` ever calling `std::fs` directly.
pub trait AssetSource {
    fn read(&self, rel_path: &str) -> Option<Vec<u8>>;
}

pub struct FsAssetSource {
    root: PathBuf,
}

impl FsAssetSource {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        FsAssetSource { root: root.into() }
    }
}

impl AssetSource for FsAssetSource {
    fn read(&self, rel_path: &str) -> Option<Vec<u8>> {
        std::fs::read(self.root.join(rel_path)).ok()
    }
}

/// Resolves a resource location like "the_vault:vault/rooms/common/bee1" to a relative path
/// like "structures/vault/rooms/common/bee1.nbt" under the given category - mirrors the
/// namespace-stripping behavior of the original `res_to_path` exactly (discards everything
/// before the first `:`), just producing a plain string key instead of joining onto an
/// OS `PathBuf` root, so the same lookup works for both a real directory and an embedded map.
pub fn res_to_rel_path(resloc: &str, category: &str, ext: &str) -> String {
    let path_part = resloc.split_once(':').map(|(_, p)| p).unwrap_or(resloc);
    format!("{category}/{path_part}.{ext}")
}
