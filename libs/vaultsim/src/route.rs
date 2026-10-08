//! Shared route planner + 3D-viewer renderer for the Routerunner sweep study.
//!
//! Movement: WALK along floors (×1) or FLY straight (×`flight_mult`) to bridge — cost(A→B) =
//! min(walk_floor_path, straight×mult + up/solid/constriction penalties). Plan = greedy value/cost
//! break SELECTION (deterministic chain clears + MVT bail) → NN+2-opt hybrid ORDER (entrance→exit) →
//! walk legs (floor paths) + fly legs (straight). `render_html` emits a self-contained Three.js page
//! that references a LOCAL `three.min.js` (no CDN — fixes blank pages over file://).

use std::collections::HashSet;

use crate::chain;
use crate::pathfind::WalkGraph;
use crate::voxel::VoxelGrid;

pub type P = (i32, i32, i32);

pub const UP_PEN: f64 = 2.0;
pub const CONSTRICT_PEN: f64 = 1.5;
pub const REACH: f64 = 6.0;

pub fn euclid(a: P, b: P) -> f64 {
    let (dx, dy, dz) = ((a.0 - b.0) as f64, (a.1 - b.1) as f64, (a.2 - b.2) as f64);
    (dx * dx + dy * dy + dz * dz).sqrt()
}
fn tight(p: P, solid: &VoxelGrid) -> bool {
    for dx in -1..=1 {
        for dz in -1..=1 {
            if (dx != 0 || dz != 0) && solid.is_solid((p.0 + dx, p.1, p.2 + dz)) {
                return true;
            }
        }
    }
    false
}
fn sample_line(a: P, b: P, solid: &VoxelGrid) -> (u32, u32) {
    let steps = euclid(a, b).ceil() as i32;
    let (mut sc, mut tc) = (0, 0);
    for i in 1..steps {
        let t = i as f64 / steps as f64;
        let p = (
            (a.0 as f64 + (b.0 - a.0) as f64 * t).round() as i32,
            (a.1 as f64 + (b.1 - a.1) as f64 * t).round() as i32,
            (a.2 as f64 + (b.2 - a.2) as f64 * t).round() as i32,
        );
        if solid.is_solid(p) {
            sc += 1;
        } else if tight(p, solid) {
            tc += 1;
        }
    }
    (sc, tc)
}
fn flight_cost(a: P, b: P, solid: &VoxelGrid, mult: f64) -> f64 {
    let (sc, tc) = sample_line(a, b, solid);
    if sc > 0 {
        return f64::INFINITY; // can't fly through solid — LOS must be clear; walk routes around instead
    }
    let up = (b.1 - a.1).max(0) as f64 * UP_PEN;
    mult * euclid(a, b) + up + CONSTRICT_PEN * tc as f64
}
fn sel_cost(a: P, b: P) -> f64 {
    (euclid(a, b) - REACH).max(0.5) + (b.1 - a.1).max(0) as f64 * UP_PEN
}

pub struct RoutePlan {
    pub breaks: Vec<usize>,
    pub collected: usize,
    pub order: Vec<P>,
    pub segments: Vec<(char, Vec<P>)>,
    pub walk_b: f64,
    pub fly_b: f64,
    pub fly_solid: u32,
    pub state: Vec<&'static str>,
}

/// Greedy break SELECTION over `pts` (target-chest positions) with deterministic chain clears + bail.
/// Returns (break indices, collected count, remaining mask). Reusable for bail sweeps.
pub fn select(pts: &[P], buckets: &std::collections::HashMap<P, Vec<u32>>, entrance: P, bail: f64) -> (Vec<usize>, usize, Vec<bool>) {
    let mut remaining = vec![true; pts.len()];
    let mut breaks = Vec::new();
    let mut collected = 0usize;
    let mut cur = entrance;
    loop {
        let mut best = None;
        let mut bs = f64::NEG_INFINITY;
        for idx in 0..pts.len() {
            if !remaining[idx] {
                continue;
            }
            let sc = chain::clear_proxy(idx, pts, buckets, &remaining) as f64 / sel_cost(cur, pts[idx]);
            if sc > bs {
                bs = sc;
                best = Some(idx);
            }
        }
        let b = match best {
            Some(x) => x,
            None => break,
        };
        if collected > 0 && bail > 0.0 && bs < bail {
            break;
        }
        for j in chain::clear_from(b, pts, buckets, &remaining) {
            remaining[j] = false;
            collected += 1;
        }
        breaks.push(b);
        cur = pts[b];
    }
    (breaks, collected, remaining)
}

pub fn plan_route(solid: &VoxelGrid, pts: &[P], entrance: P, exit: P, flight_mult: f64, bail: f64) -> RoutePlan {
    let buckets = chain::build_buckets(pts);
    let (breaks, _, _) = select(pts, &buckets, entrance, bail);

    let graph = WalkGraph::build(solid);
    let mut nodes: Vec<P> = vec![entrance];
    nodes.extend(breaks.iter().map(|&i| pts[i]));
    nodes.push(exit);
    let n = nodes.len();
    let access: Vec<Option<u32>> = nodes.iter().map(|p| graph.nearest_node(*p, 7)).collect();
    let dijk: Vec<Option<(Vec<u32>, Vec<u32>)>> = access.iter().map(|a| a.map(|nd| graph.dijkstra(nd))).collect();
    let walk_blocks = |i: usize, j: usize| -> f64 {
        match (access[j], &dijk[i]) {
            (Some(aj), Some((d, _))) if d[aj as usize] != u32::MAX => d[aj as usize] as f64 / 100.0,
            _ => f64::INFINITY,
        }
    };
    let mut cost = vec![vec![0.0f64; n]; n];
    for i in 0..n {
        for j in (i + 1)..n {
            let w = walk_blocks(i, j).min(walk_blocks(j, i));
            let f = flight_cost(nodes[i], nodes[j], solid, flight_mult);
            let c = w.min(f);
            cost[i][j] = c;
            cost[j][i] = c;
        }
    }
    let mut tour = vec![0usize];
    let mut unvis: HashSet<usize> = (1..n - 1).collect();
    let mut c = 0usize;
    while !unvis.is_empty() {
        let nx = *unvis.iter().min_by(|&&x, &&y| cost[c][x].partial_cmp(&cost[c][y]).unwrap()).unwrap();
        tour.push(nx);
        unvis.remove(&nx);
        c = nx;
    }
    tour.push(n - 1);
    if tour.len() > 3 {
        loop {
            let mut improved = false;
            for i in 1..tour.len() - 2 {
                for k in (i + 1)..tour.len() - 1 {
                    let old = cost[tour[i - 1]][tour[i]] + cost[tour[k]][tour[k + 1]];
                    let new = cost[tour[i - 1]][tour[k]] + cost[tour[i]][tour[k + 1]];
                    if new + 1e-6 < old {
                        tour[i..=k].reverse();
                        improved = true;
                    }
                }
            }
            if !improved {
                break;
            }
        }
    }

    let mut segments: Vec<(char, Vec<P>)> = Vec::new();
    let (mut wb, mut fb, mut fs) = (0.0f64, 0.0f64, 0u32);
    for w in tour.windows(2) {
        let (i, j) = (w[0], w[1]);
        let walk = walk_blocks(i, j).min(walk_blocks(j, i));
        let fly = flight_cost(nodes[i], nodes[j], solid, flight_mult);
        if walk <= fly && walk.is_finite() {
            let path = if walk_blocks(i, j) <= walk_blocks(j, i) {
                graph.path_to(&dijk[i].as_ref().unwrap().1, access[j].unwrap())
            } else {
                let mut p = graph.path_to(&dijk[j].as_ref().unwrap().1, access[i].unwrap());
                p.reverse();
                p
            };
            wb += walk;
            segments.push(('w', path));
        } else {
            let (sc, _) = sample_line(nodes[i], nodes[j], solid);
            fs += sc;
            fb += euclid(nodes[i], nodes[j]);
            segments.push(('f', vec![nodes[i], nodes[j]]));
        }
    }
    // Densify the route into ordered points, then WALK it: mine every chest you come within reach
    // of, in path order. Each break is a numbered waypoint that chain-clears its group. This unifies
    // the "active detour" breaks and the bail-route mop-up — to a HUD-following player it's just
    // "follow the points, mine chests," whether detouring or on the way out. The greedy selection
    // above SHAPES the route; this walk decides the waypoints (which is what the player actually does).
    let mut path_pts: Vec<P> = Vec::new();
    for (_, seg) in &segments {
        for w in seg.windows(2) {
            let steps = (euclid(w[0], w[1]) / 2.0).ceil().max(1.0) as i32;
            for s in 0..=steps {
                let t = s as f64 / steps as f64;
                path_pts.push((
                    (w[0].0 as f64 + (w[1].0 - w[0].0) as f64 * t).round() as i32,
                    (w[0].1 as f64 + (w[1].1 - w[0].1) as f64 * t).round() as i32,
                    (w[0].2 as f64 + (w[1].2 - w[0].2) as f64 * t).round() as i32,
                ));
            }
        }
    }
    let mut remaining = vec![true; pts.len()];
    let mut triggered = vec![false; pts.len()];
    let mut waypoints: Vec<usize> = Vec::new();
    for p in &path_pts {
        loop {
            let bk = chain::key(*p);
            let mut best: Option<usize> = None;
            let mut bestd = REACH;
            for dx in -1..=1 {
                for dy in -1..=1 {
                    for dz in -1..=1 {
                        if let Some(v) = buckets.get(&(bk.0 + dx, bk.1 + dy, bk.2 + dz)) {
                            for &ci in v {
                                let ci = ci as usize;
                                if remaining[ci] {
                                    let d = euclid(*p, pts[ci]);
                                    if d <= bestd {
                                        bestd = d;
                                        best = Some(ci);
                                    }
                                }
                            }
                        }
                    }
                }
            }
            match best {
                Some(idx) => {
                    waypoints.push(idx);
                    triggered[idx] = true;
                    for j in chain::clear_from(idx, pts, &buckets, &remaining) {
                        remaining[j] = false;
                    }
                }
                None => break,
            }
        }
    }
    let collected = pts.len() - remaining.iter().filter(|&&r| r).count();
    let order: Vec<P> = waypoints.iter().map(|&i| pts[i]).collect();
    let state: Vec<&'static str> = (0..pts.len())
        .map(|i| if triggered[i] { "break" } else if !remaining[i] { "cleared" } else { "skipped" })
        .collect();
    RoutePlan { breaks: waypoints, collected, order, segments, walk_b: wb, fly_b: fb, fly_solid: fs, state }
}

#[allow(clippy::too_many_arguments)]
pub fn render_html(
    size: P,
    terrain: &[(P, P)],
    pts: &[P],
    state: &[&str],
    segments: &[(char, Vec<P>)],
    order: &[P],
    markers: &[(P, char)],
    hud: &str,
    three_src: &str,
) -> String {
    let a3 = |p: P| format!("[{},{},{}]", p.0, p.1, p.2);
    let terr: Vec<String> = terrain.iter().map(|(mn, mx)| format!(r#"{{"min":{},"max":{}}}"#, a3(*mn), a3(*mx))).collect();
    let ch: Vec<String> = (0..pts.len()).map(|i| format!(r#"{{"p":{},"s":"{}"}}"#, a3(pts[i]), state[i])).collect();
    let seg: Vec<String> = segments
        .iter()
        .map(|(m, path)| {
            let p: Vec<String> = path.iter().map(|p| a3(*p)).collect();
            format!(r#"{{"m":"{}","p":[{}]}}"#, m, p.join(","))
        })
        .collect();
    let ord: Vec<String> = order.iter().map(|p| a3(*p)).collect();
    let mk: Vec<String> = markers.iter().map(|(p, k)| format!(r#"{{"p":{},"k":"{}"}}"#, a3(*p), k)).collect();
    let json = format!(
        r#"{{"size":{},"terrain":[{}],"chests":[{}],"segments":[{}],"order":[{}],"markers":[{}],"hud":"{}"}}"#,
        a3(size), terr.join(","), ch.join(","), seg.join(","), ord.join(","), mk.join(","), hud,
    );
    format!("{HEAD_A}{three_src}{HEAD_B}{json}{JS_TAIL}")
}

const HEAD_A: &str = r#"<!DOCTYPE html><html><head><meta charset="utf-8"><title>Routerunner</title>
<style>body{margin:0;overflow:hidden;background:#0b0e14;font-family:monospace;color:#cdd}
#hud{position:fixed;top:8px;left:8px;font-size:12px;line-height:1.6;background:rgba(0,0,0,.6);padding:8px 10px;border-radius:4px}
#leg{position:fixed;bottom:8px;left:8px;font-size:12px;background:rgba(0,0,0,.6);padding:6px 10px;border-radius:4px}</style>
</head><body><div id="hud"></div>
<div id="leg">&#9632; <span style="color:#35a7ff">break #</span> &nbsp; &#9632; <span style="color:#ffc83a">cleared</span> &nbsp; &#9632; <span style="color:#555a66">skipped</span> &nbsp; &#8212; <span style="color:#55dd66">walk</span> / <span style="color:#35c0ff">fly</span> / <span style="color:#7a8290">hall</span> &nbsp; &#9632; <span style="color:#39ff88">in</span>/<span style="color:#ff5555">out</span></div>
<script src=""#;

const HEAD_B: &str = r#""></script>
<script>
const DATA = "#;

const JS_TAIL: &str = r#";
const scene = new THREE.Scene();
const cam = new THREE.PerspectiveCamera(55, innerWidth/innerHeight, 0.1, 9000);
const rend = new THREE.WebGLRenderer({antialias:true});
rend.setSize(innerWidth, innerHeight); rend.setClearColor(0x0b0e14);
document.body.appendChild(rend.domElement);
scene.add(new THREE.AmbientLight(0xffffff, 0.8));
const dl = new THREE.DirectionalLight(0xffffff, 0.5); dl.position.set(60,120,40); scene.add(dl);
const S = DATA.size, cx = S[0]/2, cy = S[1]/2, cz = S[2]/2;
const tmat = new THREE.MeshLambertMaterial({color:0x2a3340, transparent:true, opacity:0.11, depthWrite:false});
for (const b of DATA.terrain){
  const mn=b.min, mx=b.max, dx=mx[0]-mn[0]+1, dy=mx[1]-mn[1]+1, dz=mx[2]-mn[2]+1;
  const m = new THREE.Mesh(new THREE.BoxGeometry(dx,dy,dz), tmat);
  m.position.set(mn[0]+dx/2, mn[1]+dy/2, mn[2]+dz/2); scene.add(m);
}
function box(p, color, s){
  const m = new THREE.Mesh(new THREE.BoxGeometry(s,s,s), new THREE.MeshBasicMaterial({color}));
  m.position.set(p[0]+0.5, p[1]+0.5, p[2]+0.5); scene.add(m); return m;
}
for (const c of DATA.chests){
  if (c.s==='break') box(c.p, 0x35a7ff, 1.6);
  else if (c.s==='cleared') box(c.p, 0xffc83a, 0.62);
  else box(c.p, 0x555a66, 0.5);
}
function label(p, n){
  const cv=document.createElement('canvas'); cv.width=cv.height=64;
  const g=cv.getContext('2d');
  g.fillStyle='rgba(11,14,20,0.85)'; g.beginPath(); g.arc(32,32,30,0,7); g.fill();
  g.strokeStyle='#35a7ff'; g.lineWidth=4; g.stroke();
  g.fillStyle='#cfe8ff'; g.font='bold 34px monospace'; g.textAlign='center'; g.textBaseline='middle'; g.fillText(n,32,35);
  const sp=new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(cv), depthTest:false}));
  sp.position.set(p[0]+0.5, p[1]+2.2, p[2]+0.5); sp.scale.set(2.6,2.6,1); scene.add(sp);
}
const wmat = new THREE.LineBasicMaterial({color:0x55dd66});
const fmat = new THREE.LineBasicMaterial({color:0x35c0ff});
const hmat = new THREE.LineBasicMaterial({color:0x7a8290});
for (const s of DATA.segments){
  const v = s.p.map(p=>new THREE.Vector3(p[0]+0.5, p[1]+0.7, p[2]+0.5));
  if (v.length<2) continue;
  scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(v), s.m==='w'?wmat:(s.m==='h'?hmat:fmat)));
  if (s.m==='f'){
    const a=v[0], b=v[v.length-1], dir=b.clone().sub(a), len=dir.length();
    if (len>0.6) scene.add(new THREE.ArrowHelper(dir.clone().normalize(), a.clone().add(dir.clone().multiplyScalar(0.1)), len*0.8, 0x35c0ff, Math.min(2.6,len*0.22), Math.min(1.7,len*0.14)));
  }
}
DATA.order.forEach((p,i)=>label(p, i+1));
for (const mk of DATA.markers) box(mk.p, mk.k==='i'?0x39ff88:0xff5555, 2.4);
document.getElementById('hud').innerHTML = DATA.hud;
let rot=0.7, pit=0.7, dist=Math.max(S[0],S[2])*1.4;
function upd(){ cam.position.set(cx+dist*Math.cos(pit)*Math.sin(rot), cy+dist*Math.sin(pit), cz+dist*Math.cos(pit)*Math.cos(rot)); cam.lookAt(cx,cy,cz); }
let drag=false, lx=0, ly=0;
addEventListener('mousedown', e=>{drag=true; lx=e.clientX; ly=e.clientY;});
addEventListener('mouseup', ()=>drag=false);
addEventListener('mousemove', e=>{ if(!drag) return; rot-=(e.clientX-lx)*0.008; pit=Math.max(-1.45,Math.min(1.45,pit+(e.clientY-ly)*0.008)); lx=e.clientX; ly=e.clientY; });
addEventListener('wheel', e=>{ dist=Math.max(6, dist*(1+Math.sign(e.deltaY)*0.1)); });
addEventListener('resize', ()=>{ cam.aspect=innerWidth/innerHeight; cam.updateProjectionMatrix(); rend.setSize(innerWidth,innerHeight); });
(function loop(){ upd(); rend.render(scene,cam); requestAnimationFrame(loop); })();
</script></body></html>"#;
