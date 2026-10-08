//! wvk: reads JSON on stdin (built by model/kernel.py) and writes the reply on stdout.
//!
//! Single mode {"ctx", "family", "cmd"}: cmd.op = "anneal" (one search per seed), "eval" (score given builds) or
//! "profile" (time the evaluation stages).
//! Batch mode {"ctxs", "families", "jobs"}: every job is one anneal (context, family, seed); jobs run on a thread pool
//! across all cores, longest first.

mod build;
mod eval;
mod problem;
mod search;

use build::{Build, BuildDoc, Fallbacks};
use problem::{CtxRaw, FamilyRaw, Problem};
use rayon::prelude::*;
use search::{Schedule, Search};
use serde::Deserialize;
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::sync::atomic::{AtomicUsize, Ordering};

#[derive(Deserialize)]
#[serde(tag = "op")]
enum Cmd {
    #[serde(rename = "anneal")]
    Anneal {
        iters: usize,
        seeds: Vec<u64>,
        schedule: Schedule,
        trace: bool,
        #[serde(default)]
        legacy: bool,
        #[serde(default)]
        verify_delta: bool,
    },
    #[serde(rename = "eval")]
    Eval { builds: Vec<BuildDoc> },
    #[serde(rename = "profile")]
    Profile { n: usize },
}

#[derive(Deserialize)]
struct Single {
    ctx: CtxRaw,
    family: FamilyRaw,
    cmd: Cmd,
}

#[derive(Deserialize)]
struct Job {
    ctx: usize,
    family: usize,
    iters: usize,
    seed: u64,
    schedule: Schedule,
    #[serde(default)]
    legacy: bool,
    #[serde(default)]
    trace: bool,
    #[serde(default)]
    verify_delta: bool,
}

#[derive(Deserialize)]
struct Batch {
    ctxs: Vec<CtxRaw>,
    families: Vec<FamilyRaw>,
    jobs: Vec<Job>,
    #[serde(default)]
    threads: usize,
}

struct Counts {
    fb: Fallbacks,
    no_legal: u64,
    fam: String,
}

fn fallbacks(c: &Counts) -> Value {
    let mut m = serde_json::Map::new();
    let fb = &c.fb;
    if fb.trinket_op > 0 {
        m.insert("trinket-op".into(), json!({"n": fb.trinket_op, "msg": "trinket vanilla modifier with operation != 2; treated as additive"}));
    }
    if fb.ehp_zero > 0 {
        m.insert("ehp-zero-mult".into(), json!({"n": fb.ehp_zero, "msg": "damage-taken multiplier hit ~0; clamped to 1e-9"}));
    }
    if fb.eval_error > 0 {
        m.insert("eval-error".into(), json!({"n": fb.eval_error, "msg": format!("{}: {}", c.fam, fb.first_error.clone().unwrap_or_default())}));
    }
    if c.no_legal > 0 {
        m.insert("no-legal-move".into(), json!({"n": c.no_legal, "msg": "500 draws without a legal, changed candidate; kept the current build"}));
    }
    for (i, &u) in fb.holes_used.iter().enumerate() {
        if u {
            m.insert(format!("hole:{}", i), json!({"n": 1, "msg": ""}));
        }
    }
    Value::Object(m)
}

fn counts(s: &Search) -> Counts {
    Counts { fb: s.fb.clone(), no_legal: s.no_legal, fam: s.p.fam.id.clone() }
}

fn run_single(inp: Single) -> Value {
    let p = Problem::new(inp.ctx, inp.family);
    let mut s = Search::new(&p);
    match inp.cmd {
        Cmd::Anneal { iters, seeds, schedule, trace, legacy, verify_delta } => {
            s.verify_delta = verify_delta;
            let mut results = Vec::new();
            for seed in seeds {
                let (b, sc, tr) = s.anneal(iters, seed, &schedule, trace, legacy);
                let mut r = json!({"seed": seed, "score": sc, "build": b.to_doc(&p), "evals": tr.evals, "secs": tr.secs});
                if trace {
                    r["trace"] = serde_json::to_value(&tr).unwrap();
                }
                results.push(r);
            }
            json!({"results": results, "fallbacks": fallbacks(&counts(&s)),
                   "delta": {"checked": s.delta_checked, "mismatch": s.delta_mismatch, "max_err": s.delta_max_err}})
        }
        Cmd::Eval { builds } => {
            let mut results = Vec::new();
            for d in &builds {
                let b = Build::from_doc(d, &p);
                match eval::evaluate(&b, &p, &mut s.fb) {
                    Ok(sc) => results.push(json!({"ok": true, "score": sc.score, "cycle": sc.cycle, "cycle_damage": sc.cycle_damage,
                                                  "cycle_survival": sc.cycle_survival, "dps": sc.dps, "pack_dps": sc.pack_dps, "ehp": sc.ehp})),
                    Err(e) => results.push(json!({"ok": false, "error": e})),
                }
            }
            json!({"results": results, "fallbacks": fallbacks(&counts(&s))})
        }
        Cmd::Profile { n } => profile(&p, &mut s, n),
    }
}

fn run_batch(inp: Batch) -> Value {
    if inp.threads > 0 {
        rayon::ThreadPoolBuilder::new().num_threads(inp.threads).build_global().ok();
    }
    let base: Vec<Problem> = inp.ctxs.into_iter().map(|c| Problem::new(c, inp.families[0].clone())).collect();
    let mut order: Vec<usize> = (0..inp.jobs.len()).collect();
    order.sort_by_key(|&k| std::cmp::Reverse(inp.jobs[k].iters));
    let done = AtomicUsize::new(0);
    let total = inp.jobs.len();
    let t0 = std::time::Instant::now();
    let mut out: Vec<(usize, usize, Value, Counts)> = order.par_iter().map(|&k| {
        let j = &inp.jobs[k];
        let p = base[j.ctx].with_family(&inp.families[j.family]);
        let mut s = Search::new(&p);
        s.verify_delta = j.verify_delta;
        let (b, sc, tr) = s.anneal(j.iters, j.seed, &j.schedule, j.trace, j.legacy);
        let mut r = json!({"job": k, "score": sc, "build": b.to_doc(&p), "evals": tr.evals, "secs": tr.secs});
        if j.trace {
            r["trace"] = serde_json::to_value(&tr).unwrap();
        }
        if j.verify_delta {
            r["delta"] = json!({"checked": s.delta_checked, "mismatch": s.delta_mismatch, "max_err": s.delta_max_err});
        }
        let n = done.fetch_add(1, Ordering::Relaxed) + 1;
        if n % (total / 10).max(1) == 0 || n == total {
            eprintln!("[wvk] {}/{} jobs, {:.1}s", n, total, t0.elapsed().as_secs_f64());
        }
        (k, j.ctx, r, counts(&s))
    }).collect();
    out.sort_by_key(|x| x.0);
    let mut per_ctx: Vec<Counts> = (0..base.len()).map(|_| Counts { fb: Fallbacks::default(), no_legal: 0, fam: String::new() }).collect();
    for (_, c, _, cnt) in &out {
        per_ctx[*c].fb.merge(&cnt.fb);
        per_ctx[*c].no_legal += cnt.no_legal;
        if per_ctx[*c].fam.is_empty() && cnt.fb.eval_error > 0 {
            per_ctx[*c].fam = cnt.fam.clone();
        }
    }
    json!({"results": out.into_iter().map(|x| x.2).collect::<Vec<_>>(),
           "fallbacks": per_ctx.iter().map(fallbacks).collect::<Vec<_>>(),
           "threads": rayon::current_num_threads(), "secs": t0.elapsed().as_secs_f64()})
}

fn main() {
    let mut buf = Vec::new();
    std::io::stdin().read_to_end(&mut buf).expect("read stdin");
    let v: Value = serde_json::from_slice(&buf).unwrap_or_else(|e| {
        eprintln!("bad input JSON: {}", e);
        std::process::exit(2);
    });
    let out = if v.get("jobs").is_some() {
        run_batch(serde_json::from_value(v).unwrap_or_else(|e| {
            eprintln!("bad batch input: {}", e);
            std::process::exit(2);
        }))
    } else {
        run_single(serde_json::from_value(v).unwrap_or_else(|e| {
            eprintln!("bad input: {}", e);
            std::process::exit(2);
        }))
    };
    let stdout = std::io::stdout();
    let mut h = stdout.lock();
    serde_json::to_writer(&mut h, &out).expect("write stdout");
    h.flush().ok();
}

/// Time each stage of one evaluation on builds sampled along a short walk (ns per call).
fn profile(p: &Problem, s: &mut Search, n: usize) -> Value {
    use std::time::Instant;
    let mut rng = search::Rng::new(7);
    let mut buf = Vec::new();
    let b0 = s.initial_build(&mut rng);
    let (mut cur, _) = s.polish(b0, 1);
    let mut pairs = Vec::with_capacity(n);
    for _ in 0..n {
        let c = s.propose(&cur, &mut rng, &mut buf);
        pairs.push((cur, c));
        if rng.f64() < 0.3 {
            cur = c;
        }
    }
    let per = |t: Instant| t.elapsed().as_nanos() as f64 / n as f64;
    let mut r2 = search::Rng::new(9);
    let t = Instant::now();
    for (a, _) in &pairs {
        std::hint::black_box(s.propose(a, &mut r2, &mut buf));
    }
    let propose_ns = per(t);
    let t = Instant::now();
    for (_, c) in &pairs {
        std::hint::black_box(build::lin_full(c, p));
    }
    let lin_full_ns = per(t);
    let lins: Vec<_> = pairs.iter().map(|(a, _)| build::lin_full(a, p)).collect();
    let t = Instant::now();
    for ((a, c), l) in pairs.iter().zip(&lins) {
        std::hint::black_box(build::lin_delta(a, l, c, p));
    }
    let lin_delta_ns = per(t);
    let clins: Vec<_> = pairs.iter().map(|(_, c)| build::lin_full(c, p)).collect();
    let t = Instant::now();
    for ((_, c), l) in pairs.iter().zip(&clins) {
        std::hint::black_box(build::stats(c, p, l, &mut s.fb));
    }
    let stats_ns = per(t);
    let sts: Vec<_> = pairs.iter().zip(&clins).map(|((_, c), l)| build::stats(c, p, l, &mut s.fb)).collect();
    let ds: Vec<_> = sts.iter().map(|st| build::derive(st, p, false)).collect();
    let t = Instant::now();
    for (((_, c), st), d) in pairs.iter().zip(&sts).zip(&ds) {
        std::hint::black_box(eval::Ev { p, b: c, st, d }.compute(&mut s.fb).ok());
    }
    let compute_ns = per(t);
    let t = Instant::now();
    for ((_, c), l) in pairs.iter().zip(&clins) {
        std::hint::black_box(eval::evaluate_lin(c, p, l, &mut s.fb).ok());
    }
    let eval_lin_ns = per(t);
    let t = Instant::now();
    for ((a, c), l) in pairs.iter().zip(&lins) {
        std::hint::black_box(s.score_from(a, l, c));
    }
    let step_ns = per(t);
    json!({"propose_ns": propose_ns, "lin_full_ns": lin_full_ns, "lin_delta_ns": lin_delta_ns, "stats_ns": stats_ns,
           "compute_ns": compute_ns, "evaluate_given_lin_ns": eval_lin_ns, "delta_score_ns": step_ns,
           "fallbacks": fallbacks(&counts(s))})
}
