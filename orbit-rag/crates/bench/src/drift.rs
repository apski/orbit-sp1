//! Drift checker accuracy: the Tidepool docs plus a small TypeScript tree whose
//! constants disagree with some docs (`benchmarks/drift_expected.json`, written
//! before the checker ran). Scored per (constant, doc file) pair.
use anyhow::Result;
use orbit_core::model::{code_model, drift, Drift};
use orbit_core::Store;
use serde::Serialize;
use serde_json::Value;
use std::collections::BTreeSet;
use std::path::Path;

#[derive(Serialize)]
pub struct Report {
    pub expected: usize,
    pub found: usize,
    pub true_positives: usize,
    pub precision: f64,
    pub recall: f64,
    /// expected pairs found with the wrong verdict (drift vs history)
    pub wrong_verdict: Vec<String>,
    pub missed: Vec<String>,
    pub false_positives: Vec<Drift>,
    pub tags: usize,
    pub subsystems: usize,
    pub ms: f64,
}

pub fn run(docs: &Path, code: &Path, expected: &Path) -> Result<Report> {
    let dir = std::env::temp_dir().join(format!("orbit-drift-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    crate::perf::copy_dir(docs, &dir)?;
    crate::perf::copy_dir(code, &dir.join("src"))?;
    let store = Store::init(&dir)?;
    for f in store.source_files()? {
        store.open_source(&dir.join(f))?;
    }
    let t = std::time::Instant::now();
    let found = drift(&store)?;
    let ms = t.elapsed().as_secs_f64() * 1e3;
    let want: Value = serde_json::from_str(&std::fs::read_to_string(expected)?)?;
    let want: BTreeSet<(String, String, String)> = want["expected"]
        .as_array()
        .unwrap()
        .iter()
        .map(|e| (e["constant"].as_str().unwrap().into(), e["doc"].as_str().unwrap().into(), e["verdict"].as_str().unwrap().into()))
        .collect();
    let doc_of = |d: &Drift| d.doc.rsplit_once(':').map_or(d.doc.clone(), |x| x.0.to_string());
    let got: BTreeSet<(String, String)> = found.iter().map(|d| (d.constant.clone(), doc_of(d))).collect();
    let tp = want.iter().filter(|(c, d, _)| got.contains(&(c.clone(), d.clone()))).count();
    let wrong_verdict = want
        .iter()
        .filter(|(c, d, v)| found.iter().any(|f| &f.constant == c && &doc_of(f) == d && &f.verdict != v))
        .map(|(c, d, v)| format!("{c} @ {d}: expected {v}"))
        .collect();
    let missed = want.iter().filter(|(c, d, _)| !got.contains(&(c.clone(), d.clone()))).map(|(c, d, _)| format!("{c} @ {d}")).collect();
    let mut fp: Vec<Drift> = found.iter().filter(|f| !want.iter().any(|(c, d, _)| c == &f.constant && d == &doc_of(f))).cloned().collect();
    fp.dedup_by(|a, b| a.constant == b.constant && a.doc == b.doc);
    let tags: usize = store.db.query_row("SELECT COUNT(*) FROM tags", [], |r| r.get(0))?;
    let subsystems = code_model(&store, None)?.len();
    let _ = std::fs::remove_dir_all(&dir);
    Ok(Report {
        expected: want.len(),
        found: got.len(),
        true_positives: tp,
        precision: tp as f64 / got.len().max(1) as f64,
        recall: tp as f64 / want.len().max(1) as f64,
        wrong_verdict,
        missed,
        false_positives: fp,
        tags,
        subsystems,
        ms,
    })
}
