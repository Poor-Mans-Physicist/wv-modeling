use std::collections::HashMap;
use std::path::Path;

use crate::nbt::{self, Tag};

#[derive(Debug, Clone)]
pub struct BlockSpec {
    pub name: String,
    pub properties: HashMap<String, String>,
}

#[derive(Debug, Clone)]
pub struct JigsawInfo {
    pub name: String,
    pub target: String,
    pub pool: String,
    pub joint: String, // "rollable" | "aligned"
    pub front: String, // direction token, e.g. "up", "north"
    pub side: String,  // direction token
    /// The blockstate the game replaces this jigsaw marker with once generation finishes (e.g.
    /// "minecraft:air", "minecraft:grass_block", "the_vault:placeholder[type=ore]"). The raw
    /// `minecraft:jigsaw` block never persists in the live world, so the voxel grid must record
    /// this resolved block, not a solid jigsaw cube - otherwise every connector (especially decor
    /// anchors, whose final_state is usually air) leaves a phantom floating solid block that chests
    /// then wrongly floor on. Defaults to "minecraft:air" if absent.
    pub final_state: String,
}

#[derive(Debug, Clone)]
pub struct BlockEntry {
    pub state: usize,
    pub pos: (i32, i32, i32),
    pub jigsaw: Option<JigsawInfo>,
}

#[derive(Debug, Clone)]
pub struct Structure {
    pub size: (i32, i32, i32),
    pub palette: Vec<BlockSpec>,
    pub blocks: Vec<BlockEntry>,
    /// Per-palette-index flag: `true` iff that block is solid AND its top face is NOT sturdy
    /// (a chest can't be floored on it). Precomputed once here, at parse time, because the
    /// classifier (`sturdy::is_sturdy_top`) does a ~50-substring scan - calling it per block in
    /// the assembly hot path (100k+ blocks/room x thousands of trials) was a real slowdown,
    /// especially single-threaded in wasm. Indexed by `BlockEntry::state`.
    pub palette_non_sturdy: Vec<bool>,
}

fn tag_list_ints(tag: &Tag) -> Vec<i32> {
    tag.as_list()
        .map(|l| l.iter().filter_map(|t| t.as_i64()).map(|v| v as i32).collect())
        .unwrap_or_default()
}

impl Structure {
    /// Parses an already-in-memory gzip-compressed NBT structure file. Pure (no I/O) so it
    /// works identically whether the bytes came from a real file or an embedded asset map.
    pub fn parse(data: &[u8]) -> std::io::Result<Structure> {
        let root = nbt::parse_gz(data)?;

        let size_vec = tag_list_ints(root.get("size").unwrap_or(&Tag::End));
        let size = (
            *size_vec.get(0).unwrap_or(&0),
            *size_vec.get(1).unwrap_or(&0),
            *size_vec.get(2).unwrap_or(&0),
        );

        let mut palette = Vec::new();
        if let Some(pal_list) = root.get("palette").and_then(Tag::as_list) {
            for entry in pal_list {
                let name = entry
                    .get("Name")
                    .and_then(Tag::as_str)
                    .unwrap_or("minecraft:air")
                    .to_string();
                let mut properties = HashMap::new();
                if let Some(props) = entry.get("Properties").and_then(Tag::as_compound) {
                    for (k, v) in props {
                        if let Some(s) = v.as_str() {
                            properties.insert(k.clone(), s.to_string());
                        }
                    }
                }
                palette.push(BlockSpec { name, properties });
            }
        }

        let mut blocks = Vec::new();
        if let Some(block_list) = root.get("blocks").and_then(Tag::as_list) {
            for entry in block_list {
                let state = entry.get("state").and_then(Tag::as_i64).unwrap_or(0) as usize;
                let pos_vec = tag_list_ints(entry.get("pos").unwrap_or(&Tag::End));
                let pos = (
                    *pos_vec.get(0).unwrap_or(&0),
                    *pos_vec.get(1).unwrap_or(&0),
                    *pos_vec.get(2).unwrap_or(&0),
                );

                let mut jigsaw = None;
                let is_jigsaw = palette
                    .get(state)
                    .map(|b| b.name == "minecraft:jigsaw")
                    .unwrap_or(false);
                if is_jigsaw {
                    if let Some(nbt_tag) = entry.get("nbt") {
                        let name = nbt_tag.get("name").and_then(Tag::as_str).unwrap_or("").to_string();
                        let target = nbt_tag.get("target").and_then(Tag::as_str).unwrap_or("").to_string();
                        let pool = nbt_tag.get("pool").and_then(Tag::as_str).unwrap_or("").to_string();
                        let joint = nbt_tag.get("joint").and_then(Tag::as_str).unwrap_or("rollable").to_string();
                        let final_state = nbt_tag
                            .get("final_state")
                            .and_then(Tag::as_str)
                            .unwrap_or("minecraft:air")
                            .to_string();
                        let orientation = palette
                            .get(state)
                            .and_then(|b| b.properties.get("orientation"))
                            .cloned()
                            .unwrap_or_else(|| "up_north".to_string());
                        let (front, side) = orientation
                            .split_once('_')
                            .map(|(a, b)| (a.to_string(), b.to_string()))
                            .unwrap_or(("up".to_string(), "north".to_string()));
                        jigsaw = Some(JigsawInfo {
                            name,
                            target,
                            pool,
                            joint,
                            front,
                            side,
                            final_state,
                        });
                    }
                }

                blocks.push(BlockEntry { state, pos, jigsaw });
            }
        }

        let palette_non_sturdy: Vec<bool> = palette
            .iter()
            .map(|s| s.name != "minecraft:air" && !crate::sturdy::is_sturdy_top(&s.name, &s.properties))
            .collect();

        Ok(Structure { size, palette, blocks, palette_non_sturdy })
    }

    /// Native convenience wrapper around `parse` for the CLI, which still loads room files
    /// directly off disk by path rather than through an `AssetSource`.
    pub fn load(path: &Path) -> std::io::Result<Structure> {
        let data = std::fs::read(path)?;
        Self::parse(&data)
    }

    pub fn jigsaw_blocks(&self) -> impl Iterator<Item = (&BlockEntry, &JigsawInfo)> {
        self.blocks.iter().filter_map(|b| b.jigsaw.as_ref().map(|j| (b, j)))
    }
}
