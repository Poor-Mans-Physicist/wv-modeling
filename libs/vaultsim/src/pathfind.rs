//! Walking graph for the *hybrid* movement model: the player walks cheaply along floors and flies
//! (3× cost) only to bridge. This module is the WALK half — a graph of standable cells (feet+head
//! air, solid floor) with step-up-1 / drop-3 edges, Dijkstra + path reconstruction. Costs are in
//! centi-blocks (100 = 1 block). The flight half lives in `wv_route` (straight-line × 3 + penalties).

use std::cmp::Reverse;
use std::collections::{BinaryHeap, HashMap};

use crate::voxel::VoxelGrid;

pub type P = (i32, i32, i32);
const CARD: u32 = 100;
const DIAG: u32 = 141;

pub struct WalkGraph {
    pub nodes: Vec<P>,
    index: HashMap<P, u32>,
    adj: Vec<Vec<(u32, u32)>>,
}

impl WalkGraph {
    pub fn build(solid: &VoxelGrid) -> WalkGraph {
        let (sx, sy, sz) = solid.size();
        let mut nodes = Vec::new();
        let mut index: HashMap<P, u32> = HashMap::new();
        for x in 0..sx {
            for z in 0..sz {
                for y in 1..sy {
                    let p = (x, y, z);
                    if !solid.is_solid(p) && !solid.is_solid((x, y + 1, z)) && solid.is_solid((x, y - 1, z)) {
                        index.insert(p, nodes.len() as u32);
                        nodes.push(p);
                    }
                }
            }
        }
        let dirs = [
            (1, 0, CARD), (-1, 0, CARD), (0, 1, CARD), (0, -1, CARD),
            (1, 1, DIAG), (1, -1, DIAG), (-1, 1, DIAG), (-1, -1, DIAG),
        ];
        let mut adj = vec![Vec::new(); nodes.len()];
        for (i, &p) in nodes.iter().enumerate() {
            for &(dx, dz, base) in &dirs {
                for dy in [1, 0, -1, -2, -3] {
                    if let Some(&j) = index.get(&(p.0 + dx, p.1 + dy, p.2 + dz)) {
                        adj[i].push((j, base));
                        break;
                    }
                }
            }
        }
        WalkGraph { nodes, index, adj }
    }

    pub fn nearest_node(&self, c: P, radius: i32) -> Option<u32> {
        let mut best = None;
        let mut bestd = i64::MAX;
        for dx in -radius..=radius {
            for dy in -radius..=radius {
                for dz in -radius..=radius {
                    if let Some(&j) = self.index.get(&(c.0 + dx, c.1 + dy, c.2 + dz)) {
                        let d = (dx as i64).pow(2) + (dy as i64).pow(2) + (dz as i64).pow(2);
                        if d < bestd {
                            bestd = d;
                            best = Some(j);
                        }
                    }
                }
            }
        }
        best
    }

    /// Dijkstra from `src` → (dist in centi-blocks, prev for path reconstruction).
    pub fn dijkstra(&self, src: u32) -> (Vec<u32>, Vec<u32>) {
        let n = self.nodes.len();
        let mut dist = vec![u32::MAX; n];
        let mut prev = vec![u32::MAX; n];
        let mut heap: BinaryHeap<Reverse<(u32, u32)>> = BinaryHeap::new();
        dist[src as usize] = 0;
        heap.push(Reverse((0, src)));
        while let Some(Reverse((d, u))) = heap.pop() {
            if d > dist[u as usize] {
                continue;
            }
            for &(v, w) in &self.adj[u as usize] {
                let nd = d.saturating_add(w);
                if nd < dist[v as usize] {
                    dist[v as usize] = nd;
                    prev[v as usize] = u;
                    heap.push(Reverse((nd, v)));
                }
            }
        }
        (dist, prev)
    }

    pub fn path_to(&self, prev: &[u32], target: u32) -> Vec<P> {
        let mut out = Vec::new();
        let mut cur = target;
        while cur != u32::MAX {
            out.push(self.nodes[cur as usize]);
            cur = prev[cur as usize];
        }
        out.reverse();
        out
    }

    pub fn pos(&self, node: u32) -> P {
        self.nodes[node as usize]
    }
}
