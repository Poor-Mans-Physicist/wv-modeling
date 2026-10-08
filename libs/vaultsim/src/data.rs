use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

use rand::Rng;

use crate::assets::{res_to_rel_path, AssetSource};
use crate::pools::{Pool, PoolEntry};
use crate::structure::Structure;

pub struct DataSource<'a> {
    assets: &'a dyn AssetSource,
    pools: RefCell<HashMap<String, Option<Rc<Pool>>>>,
    structures: RefCell<HashMap<String, Option<Rc<Structure>>>>,
}

impl<'a> DataSource<'a> {
    pub fn new(assets: &'a dyn AssetSource) -> Self {
        DataSource {
            assets,
            pools: RefCell::new(HashMap::new()),
            structures: RefCell::new(HashMap::new()),
        }
    }

    pub fn get_structure(&self, resloc: &str) -> Option<Rc<Structure>> {
        if let Some(cached) = self.structures.borrow().get(resloc) {
            return cached.clone();
        }
        let rel_path = res_to_rel_path(resloc, "structures", "nbt");
        let result = match self.assets.read(&rel_path) {
            Some(bytes) => match Structure::parse(&bytes) {
                Ok(s) => Some(Rc::new(s)),
                Err(e) => {
                    eprintln!("[data] WARNING: failed to parse structure {resloc} at {rel_path}: {e}");
                    None
                }
            },
            None => None,
        };
        self.structures
            .borrow_mut()
            .insert(resloc.to_string(), result.clone());
        result
    }

    fn get_pool(&self, resloc: &str) -> Option<Rc<Pool>> {
        if let Some(cached) = self.pools.borrow().get(resloc) {
            return cached.clone();
        }
        let rel_path = res_to_rel_path(resloc, "template_pools", "json");
        let result = match self.assets.read(&rel_path) {
            Some(bytes) => match serde_json::from_slice::<Pool>(&bytes) {
                Ok(entries) => Some(Rc::new(entries)),
                Err(e) => {
                    eprintln!("[data] WARNING: failed to parse pool {resloc} at {rel_path}: {e}");
                    None
                }
            },
            None => None,
        };
        self.pools.borrow_mut().insert(resloc.to_string(), result.clone());
        result
    }

    /// Weighted-random pick of a concrete structure resource-location from a named pool,
    /// recursively descending `reference`/`pool` indirection exactly like TemplatePool.selectEntry.
    /// Returns None if the pool doesn't exist, is empty/all-zero-weight, or resolves to
    /// "the_vault:empty".
    pub fn sample_pool(&self, resloc: &str, rng: &mut impl Rng) -> Option<String> {
        let pool = self.get_pool(resloc)?;
        self.sample_entries(&pool, rng)
    }

    fn sample_entries(&self, entries: &[PoolEntry], rng: &mut impl Rng) -> Option<String> {
        let total: u32 = entries.iter().map(|e| e.weight).sum();
        if total == 0 {
            return None;
        }
        let mut roll = rng.gen_range(0..total);
        for e in entries {
            if roll < e.weight {
                return self.resolve_entry(e, rng);
            }
            roll -= e.weight;
        }
        None
    }

    fn resolve_entry(&self, entry: &PoolEntry, rng: &mut impl Rng) -> Option<String> {
        if let Some(v) = &entry.value {
            if v.template == "the_vault:empty" {
                return None;
            }
            return Some(v.template.clone());
        }
        if let Some(r) = &entry.reference {
            return self.sample_pool(r, rng);
        }
        if let Some(p) = &entry.pool {
            return self.sample_entries(p, rng);
        }
        None
    }

    /// `sample_pool`, also returning the palettes of the chosen leaf entry (same RNG consumption).
    pub fn sample_pool_entry(&self, resloc: &str, rng: &mut impl Rng) -> Option<(String, Vec<String>)> {
        let pool = self.get_pool(resloc)?;
        self.sample_entries_full(&pool, rng)
    }

    fn sample_entries_full(&self, entries: &[PoolEntry], rng: &mut impl Rng) -> Option<(String, Vec<String>)> {
        let total: u32 = entries.iter().map(|e| e.weight).sum();
        if total == 0 {
            return None;
        }
        let mut roll = rng.gen_range(0..total);
        for e in entries {
            if roll < e.weight {
                if let Some(v) = &e.value {
                    if v.template == "the_vault:empty" {
                        return None;
                    }
                    return Some((v.template.clone(), v.palettes.clone()));
                }
                if let Some(r) = &e.reference {
                    return self.sample_pool_entry(r, rng);
                }
                if let Some(p) = &e.pool {
                    return self.sample_entries_full(p, rng);
                }
                return None;
            }
            roll -= e.weight;
        }
        None
    }

    /// Every leaf (probability, template, palettes) reachable from a pool.
    pub fn pool_leaves(&self, resloc: &str) -> Vec<(f64, String, Vec<String>)> {
        let mut out = Vec::new();
        if let Some(pool) = self.get_pool(resloc) {
            self.leaves_of(&pool, 1.0, &mut out);
        }
        out
    }

    fn leaves_of(&self, entries: &[PoolEntry], mass: f64, out: &mut Vec<(f64, String, Vec<String>)>) {
        let total: u32 = entries.iter().map(|e| e.weight).sum();
        if total == 0 {
            return;
        }
        for e in entries {
            let m = mass * e.weight as f64 / total as f64;
            if let Some(v) = &e.value {
                out.push((m, v.template.clone(), v.palettes.clone()));
            } else if let Some(r) = &e.reference {
                if let Some(pool) = self.get_pool(r) {
                    self.leaves_of(&pool, m, out);
                }
            } else if let Some(p) = &e.pool {
                self.leaves_of(p, m, out);
            }
        }
    }

    /// Exact probability distribution over every reachable concrete template in a pool -
    /// recursively resolves `reference`/`pool` indirection the same way `sample_pool` does
    /// stochastically, but returns every leaf's overall reachability probability instead of
    /// randomly picking one. Second return value is the probability mass dropped to
    /// "the_vault:empty" or an unresolvable reference/pool (the map's own values sum to
    /// `1.0 - dropped`, not necessarily 1.0).
    pub fn resolve_distribution(&self, resloc: &str) -> (HashMap<String, f64>, f64) {
        match self.get_pool(resloc) {
            Some(pool) => self.distribution_of_entries(&pool),
            None => (HashMap::new(), 1.0),
        }
    }

    fn distribution_of_entries(&self, entries: &[PoolEntry]) -> (HashMap<String, f64>, f64) {
        let total: u32 = entries.iter().map(|e| e.weight).sum();
        if total == 0 {
            return (HashMap::new(), 1.0);
        }
        let mut result: HashMap<String, f64> = HashMap::new();
        let mut dropped = 0.0;
        for e in entries {
            let frac = e.weight as f64 / total as f64;
            if let Some(v) = &e.value {
                if v.template == "the_vault:empty" {
                    dropped += frac;
                } else {
                    *result.entry(v.template.clone()).or_insert(0.0) += frac;
                }
            } else if let Some(r) = &e.reference {
                let (sub, sub_dropped) = self.resolve_distribution(r);
                for (k, v) in sub {
                    *result.entry(k).or_insert(0.0) += frac * v;
                }
                dropped += frac * sub_dropped;
            } else if let Some(p) = &e.pool {
                let (sub, sub_dropped) = self.distribution_of_entries(p);
                for (k, v) in sub {
                    *result.entry(k).or_insert(0.0) += frac * v;
                }
                dropped += frac * sub_dropped;
            } else {
                dropped += frac;
            }
        }
        (result, dropped)
    }
}
