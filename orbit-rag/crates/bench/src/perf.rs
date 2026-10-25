//! Latency gates from the plan (§9). Warm = in-process, repeated; cold = a fresh
//! `orbit-rag` process per call. Query embeddings are precomputed for warm runs, so
//! inference is excluded; cold runs use ORBIT_EMBED=none (lexical) for the same reason.
use crate::retrieval::Query;
use crate::util::{stats, time, Rng, Stats};
use anyhow::{Context, Result};
use orbit_core::bundles::bundle_with;
use orbit_core::embed::{Embedder, Role};
use orbit_core::retrieval::{search_with, Lexical, Retrievers, SearchQuery, Vector};
use orbit_core::Store;
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

#[derive(Serialize)]
pub struct Gate {
    pub operation: String,
    pub mode: String,
    pub target_p95_ms: Option<f64>,
    pub stats: Stats,
    pub pass: Option<bool>,
}

pub fn copy_dir(from: &Path, to: &Path) -> Result<()> {
    std::fs::create_dir_all(to)?;
    for e in std::fs::read_dir(from)? {
        let e = e?;
        let (src, dst) = (e.path(), to.join(e.file_name()));
        if e.file_type()?.is_dir() {
            copy_dir(&src, &dst)?;
        } else {
            std::fs::copy(&src, &dst)?;
        }
    }
    Ok(())
}

fn gate(op: &str, mode: &str, target: Option<f64>, d: Vec<Duration>) -> Gate {
    let s = stats(d);
    let pass = target.map(|t| s.p95_ms < t);
    eprintln!("{op:<28} {mode:<5} p50 {:>7.2} ms  p95 {:>7.2} ms  max {:>7.2} ms  {}", s.p50_ms, s.p95_ms, s.max_ms,
        pass.map_or(String::new(), |p| format!("target {:.0} ms {}", target.unwrap(), if p { "PASS" } else { "FAIL" })));
    Gate { operation: op.into(), mode: mode.into(), target_p95_ms: target, stats: s, pass }
}

pub fn run(corpus: &Path, queries: &[Query], orbit_bin: &Path, iters: usize) -> Result<Vec<Gate>> {
    let tmp = std::env::temp_dir().join(format!("orbit-perf-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&tmp);
    copy_dir(corpus, &tmp)?;
    let store = Store::discover(&tmp)?;
    let embedder = Embedder::from_env()?.context("perf needs an embedder")?;
    let qs: Vec<&Query> = queries.iter().cycle().take(iters).collect();
    let vecs = embedder.embed(&queries.iter().map(|q| q.query.clone()).collect::<Vec<_>>(), Role::Query)?;
    let vec_of = |q: &Query| vecs[q.qid].clone();
    let sq = |q: &Query, limit| SearchQuery { text: q.query.clone(), limit, vector: Some(vec_of(q)), ..Default::default() };
    let mut gates = Vec::new();

    let lexical: Retrievers = vec![Box::new(Lexical(&store))];
    let hybrid: Retrievers = vec![Box::new(Lexical(&store)), Box::new(Vector { store: &store, embedder: &embedder })];
    let warm = |rs: &Retrievers, f: &dyn Fn(&Retrievers, &Query) -> Result<()>| -> Result<Vec<Duration>> {
        for q in qs.iter().take(10) {
            f(rs, q)?;
        }
        qs.iter().map(|q| { let (r, d) = time(|| f(rs, q)); r.map(|_| d) }).collect()
    };
    let search = |rs: &Retrievers, q: &Query| search_with(&store, rs, &sq(q, 10)).map(|_| ());
    let bundle = |rs: &Retrievers, q: &Query| bundle_with(&store, rs, &SearchQuery { text: q.query.clone(), vector: Some(vec_of(q)), ..Default::default() }, 4000).map(|_| ());

    gates.push(gate("lexical retrieval", "warm", Some(30.0), warm(&lexical, &search)?));
    gates.push(gate("hybrid retrieval (sqlite)", "warm", Some(150.0), warm(&hybrid, &search)?));
    #[cfg(feature = "lance")]
    {
        let ix = orbit_lance::LanceIndex::open(&store, &embedder)?;
        let lh: Retrievers = vec![Box::new(ix.retriever(orbit_lance::Mode::Hybrid, &embedder))];
        gates.push(gate("hybrid retrieval (lance)", "warm", Some(150.0), warm(&lh, &search)?));
    }
    gates.push(gate("4k-token bundle", "warm", Some(200.0), warm(&hybrid, &bundle)?));

    // highlight save: word-aligned spans inside the labelled passages
    let mut rng = Rng::new(7);
    let mut saves = Vec::new();
    for q in &qs {
        let src = store.source_by_path(&q.path)?.context("source")?;
        let rev = store.latest_revision(&src)?.unwrap();
        let text = store.snapshot(&rev.snapshot_hash)?;
        let words: Vec<usize> = text[q.start..q.end].match_indices(' ').map(|(i, _)| q.start + i + 1).collect();
        if words.len() < 12 {
            continue;
        }
        let i = rng.below(words.len() - 10);
        let (s, e) = (words[i], words[i + 6] - 1);
        let (r, d) = time(|| store.capture(&src, rev.id, s, e, "insight", None, None, false));
        r?;
        saves.push(d);
    }
    gates.push(gate("highlight save", "warm", Some(50.0), saves));

    // open typical Markdown file: the viewer's GET /api/doc against a live server
    let port = 7400 + (std::process::id() % 500) as u16;
    let mut server = Command::new(orbit_bin)
        .args(["viewer", "--no-open", "--port", &port.to_string()])
        .current_dir(&tmp)
        .env("ORBIT_EMBED", "none")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;
    let base = format!("http://127.0.0.1:{port}");
    let up = Instant::now();
    while ureq::get(&format!("{base}/api/files")).call().is_err() {
        anyhow::ensure!(up.elapsed() < Duration::from_secs(20), "viewer server did not start");
        std::thread::sleep(Duration::from_millis(50));
    }
    let open = |p: &str| ureq::get(&format!("{base}/api/doc")).query("path", p).call().map(|r| r.into_string());
    let paths: Vec<&str> = qs.iter().map(|q| q.path.as_str()).collect();
    for p in paths.iter().take(10) {
        open(p)??;
    }
    let mut opens = Vec::new();
    for p in &paths {
        let (r, d) = time(|| open(p));
        r??;
        opens.push(d);
    }
    let _ = server.kill();
    gates.push(gate("open Markdown file (HTTP)", "warm", Some(200.0), opens));

    // cold: one fresh process per call (includes process start, store open, sync of all sources)
    let cold = |args: &[&str]| -> Result<Vec<Duration>> {
        qs.iter().take(20).map(|q| {
            let mut a: Vec<String> = args.iter().map(|s| s.to_string()).collect();
            for x in a.iter_mut() { if x == "{q}" { *x = q.query.clone(); } }
            let (o, d) = time(|| Command::new(orbit_bin).args(&a).current_dir(&tmp).env("ORBIT_EMBED", "none").output());
            anyhow::ensure!(o?.status.success(), "orbit-rag {args:?} failed");
            Ok(d)
        }).collect()
    };
    gates.push(gate("lexical search (CLI)", "cold", None, cold(&["context", "search", "{q}", "--json"])?));
    gates.push(gate("4k bundle, lexical (CLI)", "cold", None, cold(&["context", "bundle", "--task", "{q}", "--tokens", "4000", "--format", "json"])?));
    let _ = std::fs::remove_dir_all(&tmp);
    Ok(gates)
}

pub fn orbit_bin() -> PathBuf {
    std::env::current_exe().ok().and_then(|p| p.parent().map(|d| d.join("orbit-rag"))).unwrap_or_else(|| "orbit-rag".into())
}
