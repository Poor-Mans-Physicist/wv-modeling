use serde::Deserialize;

#[derive(Debug, Deserialize, Clone)]
pub struct ValueEntry {
    pub template: String,
    #[serde(default)]
    pub palettes: Vec<String>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct PoolEntry {
    pub weight: u32,
    #[serde(default)]
    pub value: Option<ValueEntry>,
    #[serde(default)]
    pub reference: Option<String>,
    #[serde(default)]
    pub pool: Option<Vec<PoolEntry>>,
}

pub type Pool = Vec<PoolEntry>;
