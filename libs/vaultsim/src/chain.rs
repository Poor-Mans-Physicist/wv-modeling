//! The verified chain-miner mechanic (woldsvaults `ChainBreakHandler.areaDig`): a deterministic
//! BFS flood-fill over a SINGLE block type — FIFO queue, each node scans its Chebyshev<=RANGE box
//! nearest-first (Manhattan), capped at CAP. No RNG. See routerunner planning doc §2.5.

use std::collections::{HashMap, HashSet, VecDeque};

pub type P = (i32, i32, i32);
pub const RANGE: i32 = 6; // max-level chain range (Chebyshev half-extent)
pub const CAP: usize = 32; // max-level blockLimit (hard cap, no AoE)
const BUCKET: i32 = RANGE + 1;

pub fn key(p: P) -> P {
    (p.0.div_euclid(BUCKET), p.1.div_euclid(BUCKET), p.2.div_euclid(BUCKET))
}
pub fn cheb(a: P, b: P) -> i32 {
    (a.0 - b.0).abs().max((a.1 - b.1).abs()).max((a.2 - b.2).abs())
}
fn manh(a: P, b: P) -> i32 {
    (a.0 - b.0).abs() + (a.1 - b.1).abs() + (a.2 - b.2).abs()
}

pub fn build_buckets(pts: &[P]) -> HashMap<P, Vec<u32>> {
    let mut b: HashMap<P, Vec<u32>> = HashMap::new();
    for (i, p) in pts.iter().enumerate() {
        b.entry(key(*p)).or_default().push(i as u32);
    }
    b
}

/// Indices of still-`remaining` chests within Chebyshev<=RANGE of `pts[idx]` (excluding idx).
pub fn neighbors(idx: usize, pts: &[P], buckets: &HashMap<P, Vec<u32>>, remaining: &[bool]) -> Vec<usize> {
    let p = pts[idx];
    let b = key(p);
    let mut out = Vec::new();
    for dx in -1..=1 {
        for dy in -1..=1 {
            for dz in -1..=1 {
                if let Some(m) = buckets.get(&(b.0 + dx, b.1 + dy, b.2 + dz)) {
                    for &j in m {
                        let j = j as usize;
                        if remaining[j] && j != idx && cheb(p, pts[j]) <= RANGE {
                            out.push(j);
                        }
                    }
                }
            }
        }
    }
    out
}

/// Faithful `areaDig`: from `start`, FIFO BFS, each node scans Chebyshev<=RANGE nearest-first, cap CAP.
/// Returns the indices cleared (the chests one trigger at `start` removes).
pub fn clear_from(start: usize, pts: &[P], buckets: &HashMap<P, Vec<u32>>, remaining: &[bool]) -> Vec<usize> {
    let mut trav: Vec<usize> = Vec::new();
    let mut inset: HashSet<usize> = HashSet::new();
    let mut q: VecDeque<usize> = VecDeque::new();
    q.push_back(start);
    'outer: while let Some(head) = q.pop_front() {
        let mut cand = neighbors(head, pts, buckets, remaining);
        cand.push(head); // the head's own cell (distance 0) is in its scan box
        cand.sort_by_key(|&j| manh(pts[j], pts[head]));
        for j in cand {
            if trav.len() >= CAP {
                break 'outer;
            }
            if inset.contains(&j) || !remaining[j] {
                continue;
            }
            trav.push(j);
            inset.insert(j);
            q.push_back(j);
        }
    }
    trav
}

/// Local-density proxy for greedy break selection: how many chests a trigger at `idx` would clear
/// (own + Chebyshev-neighbours), capped at CAP. Cheap; the exact set comes from `clear_from`.
pub fn clear_proxy(idx: usize, pts: &[P], buckets: &HashMap<P, Vec<u32>>, remaining: &[bool]) -> usize {
    (neighbors(idx, pts, buckets, remaining).len() + 1).min(CAP)
}
