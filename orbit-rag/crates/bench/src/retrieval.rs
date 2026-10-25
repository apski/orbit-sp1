//! Retrieval benchmark over a real corpus: labelled queries (doc2query by a local
//! LLM, one source passage each), every engine config scored on the same data.
use crate::util::{peak_rss_mb, stats, time, Rng, Stats};
use anyhow::{bail, Context, Result};
use orbit_core::embed::{Embedder, Role};
use orbit_core::retrieval::{search_with, Corpus, Lexical, Retrievers, SearchQuery, Vector};
use orbit_core::Store;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::collections::HashSet;
use std::path::Path;
use std::time::Duration;

#[derive(Serialize, Deserialize, Clone)]
pub struct Query {
    pub qid: usize,
    pub query: String,
    pub path: String,
    pub start: usize,
    pub end: usize,
    pub heading: String,
    /// share of the query's content words that appear verbatim in the passage
    pub lexical_overlap: f64,
    /// eval split (tune / held) when a set is used to tune ranking
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub split: Option<String>,
    /// other passages that also answer it (path, start, end), e.g. a decision and its spec section
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub also: Vec<(String, usize, usize)>,
}

/// Open (or create) the corpus store, register every Markdown file, embed all.
pub fn setup(corpus: &Path) -> Result<Store> {
    let store = if (corpus.join(orbit_core::storage::DATA_DIR).is_dir() || corpus.join(".orbit/orbit.db").is_file()) { Store::discover(corpus)? } else { Store::init(corpus)? };
    let files = store.markdown_files()?;
    for f in &files {
        store.open_source(&store.root.join(f))?;
    }
    if let Some(e) = Embedder::from_env()? {
        let pending: i64 = store.db.query_row("SELECT COUNT(*) FROM embed_outbox", [], |r| r.get(0))?;
        if pending > 0 {
            eprintln!("embedding {pending} items with {} …", e.id());
        }
        store.index_pending(&e)?;
    }
    Ok(store)
}

fn content_words(s: &str) -> Vec<String> {
    const STOP: &[&str] = &["the", "a", "an", "of", "to", "in", "is", "and", "or", "for", "on", "how", "what", "why", "when", "does", "do", "can", "i", "my", "with", "be", "it", "that", "this", "are", "you", "should", "which", "use", "using", "into", "from", "as", "by", "if", "about", "between"];
    s.to_lowercase().split(|c: char| !c.is_alphanumeric()).filter(|w| w.len() > 1 && !STOP.contains(w)).map(String::from).collect()
}

fn overlap(query: &str, passage: &str) -> f64 {
    let p: HashSet<String> = content_words(passage).into_iter().collect();
    let q = content_words(query);
    if q.is_empty() { 0.0 } else { q.iter().filter(|w| p.contains(*w)).count() as f64 / q.len() as f64 }
}

fn ollama_generate(model: &str, prompt: &str, seed: u64) -> Result<String> {
    let url = std::env::var("OLLAMA_HOST").unwrap_or_else(|_| "http://127.0.0.1:11434".into());
    let v: serde_json::Value = ureq::post(&format!("{url}/api/generate"))
        .timeout(Duration::from_secs(600))
        .send_json(json!({ "model": model, "prompt": prompt, "stream": false, "think": false,
                           "options": { "temperature": 0.4, "seed": seed, "num_predict": 80 } }))?
        .into_json()?;
    Ok(v["response"].as_str().unwrap_or("").to_string())
}

pub fn gen_queries(store: &Store, n: usize, model: &str, seed: u64, include: &[String], docs_only: bool) -> Result<Vec<Query>> {
    let mut st = store.db.prepare(
        "SELECT s.path, c.start_offset, c.end_offset, c.text, c.heading_path FROM chunks c
         JOIN source_revisions r ON r.id = c.revision_id JOIN sources s ON s.id = r.source_id
         WHERE length(c.text) BETWEEN 400 AND 1700 AND substr(trim(c.text), 1, 3) <> '```' AND (?1 = 0 OR c.lang = 'markdown') ORDER BY c.id",
    )?;
    let pool: Vec<(String, usize, usize, String, String)> = st
        .query_map([docs_only as i64], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)))?
        .collect::<rusqlite::Result<Vec<(String, usize, usize, String, String)>>>()?
        .into_iter()
        .filter(|p| include.is_empty() || include.iter().any(|i| p.0.starts_with(i.as_str())))
        .collect();
    let mut rng = Rng::new(seed);
    let mut used = HashSet::new();
    let mut out = Vec::new();
    while out.len() < n && used.len() < pool.len() {
        let i = rng.below(pool.len());
        if !used.insert(i) {
            continue;
        }
        let (path, start, end, text, heading) = pool[i].clone();
        let prompt = format!(
            "Below is a passage from technical documentation.\n\nWrite ONE question a developer might type into a search box that this passage answers.\n\
             Rules: under 18 words; paraphrase in your own words, do not copy phrases or code identifiers from the passage; no quotation marks.\n\
             Output only the question.\n\nPASSAGE:\n{text}\n"
        );
        let q = ollama_generate(model, &prompt, seed + i as u64)?;
        let q = q.lines().map(str::trim).find(|l| !l.is_empty()).unwrap_or("").trim_matches(|c| c == '"' || c == '*').to_string();
        if q.split_whitespace().count() < 4 {
            continue;
        }
        eprintln!("[{}/{n}] {q}", out.len() + 1);
        let split = Some(if out.len() % 2 == 0 { "tune" } else { "held" }.to_string());
        out.push(Query { qid: out.len(), lexical_overlap: overlap(&q, &text), query: q, path, start, end, heading, split, also: vec![] });
    }
    Ok(out)
}

#[derive(Serialize)]
pub struct ConfigResult {
    pub config: String,
    pub queries: usize,
    pub recall_at_1: f64,
    pub recall_at_5: f64,
    pub recall_at_10: f64,
    pub mrr_at_10: f64,
    pub ndcg_at_10: f64,
    pub latency: Stats,
    pub setup_ms: f64,
    pub peak_rss_mb: f64,
    pub index_bytes: u64,
    pub notes: String,
    /// per query: rank of the first relevant hit (None = not in top 10) and the top 3 paths
    pub details: Vec<serde_json::Value>,
}

/// Bytes used by named tables/indexes (dbstat), falling back to the whole file.
fn sqlite_bytes(store: &Store, like: &[&str]) -> u64 {
    let q = format!("SELECT COALESCE(SUM(pgsize), 0) FROM dbstat WHERE {}", like.iter().map(|l| format!("name LIKE '{l}'")).collect::<Vec<_>>().join(" OR "));
    store.db.query_row(&q, [], |r| r.get::<_, i64>(0)).map(|b| b as u64).unwrap_or_else(|_| std::fs::metadata(store.dir().join("orbit.db")).map_or(0, |m| m.len()))
}

fn relevant(store: &Store, q: &Query) -> Result<HashSet<String>> {
    let mut st = store.db.prepare(
        "SELECT 'chunk_' || c.id FROM chunks c JOIN source_revisions r ON r.id = c.revision_id JOIN sources s ON s.id = r.source_id
         WHERE s.path = ?1 AND c.start_offset < ?3 AND c.end_offset > ?2",
    )?;
    let mut ids = HashSet::new();
    for (path, start, end) in std::iter::once((q.path.clone(), q.start, q.end)).chain(q.also.iter().cloned()) {
        for id in st.query_map(rusqlite::params![path, start, end], |r| r.get::<_, String>(0))? {
            ids.insert(id?);
        }
    }
    Ok(ids)
}

pub fn evaluate(store: &Store, config: &str, queries: &[Query]) -> Result<ConfigResult> {
    let embedder = Embedder::from_env()?.context("benchmark needs an embedder (ORBIT_EMBED)")?;
    let (vecs, embed_t) = time(|| embedder.embed(&queries.iter().map(|q| q.query.clone()).collect::<Vec<_>>(), Role::Query));
    let vecs = vecs?;
    eprintln!("{config}: embedded {} queries in {:.0} ms (excluded from latency)", queries.len(), embed_t.as_secs_f64() * 1e3);

    #[cfg(feature = "lance")]
    let lance = if config.starts_with("lance") { Some(time(|| orbit_lance::LanceIndex::open(store, &embedder))) } else { None };
    #[cfg(feature = "lance")]
    let (lance, setup) = match lance {
        Some((ix, t)) => (Some(ix?), t),
        None => (None, Duration::ZERO),
    };
    #[cfg(not(feature = "lance"))]
    let setup = Duration::ZERO;

    // `<config>+rerank:<model>` adds the cross-encoder stage over the fused hits
    let (config, rerank) = config.split_once("+rerank:").map_or((config, None), |(c, m)| (c, Some(m)));
    #[cfg(feature = "rerank")]
    if let Some(m) = rerank {
        orbit_core::retrieval::set_reranker(Box::new(orbit_rerank::CrossEncoder::open(m)?));
    }
    #[cfg(not(feature = "rerank"))]
    anyhow::ensure!(rerank.is_none(), "built without --features rerank");
    let (retrievers, index_bytes, notes): (Retrievers, u64, String) = match config {
        "fts5" => (vec![Box::new(Lexical(store))], sqlite_bytes(store, &["chunks_fts%", "records_fts%", "diffs_fts%"]), "SQLite FTS5 bm25, porter".into()),
        "sqlite-vector" => (vec![Box::new(Vector { store, embedder: &embedder })], sqlite_bytes(store, &["embeddings%"]), "brute-force cosine over SQLite blobs".into()),
        "sqlite-hybrid" => (
            vec![Box::new(Lexical(store)), Box::new(Vector { store, embedder: &embedder })],
            sqlite_bytes(store, &["chunks_fts%", "records_fts%", "diffs_fts%", "embeddings%"]),
            "FTS5 + vector, orbit RRF k=60".into(),
        ),
        #[cfg(feature = "ann")]
        c @ ("sqlite-ann-vector" | "sqlite-ann-hybrid") => {
            std::env::set_var("ORBIT_ANN_MIN", "0"); // force the HNSW path on a small corpus
            drop(orbit_core::ann::open(store, &embedder.id())?); // build outside the timed loop
            let v: Retrievers = if c == "sqlite-ann-vector" {
                vec![Box::new(Vector { store, embedder: &embedder })]
            } else {
                vec![Box::new(Lexical(store)), Box::new(Vector { store, embedder: &embedder })]
            };
            let bytes = std::fs::read_dir(store.dir().join("ann"))?.filter_map(|e| e.ok()?.metadata().ok()).map(|m| m.len()).sum();
            (v, bytes, "usearch HNSW (bf16, M=16) nominates, exact re-score from SQLite".into())
        }
        #[cfg(feature = "lance")]
        c @ ("lance-fts" | "lance-vector" | "lance-hybrid") => {
            let ix = lance.as_ref().unwrap();
            let mode = match c {
                "lance-fts" => orbit_lance::Mode::Lexical,
                "lance-vector" => orbit_lance::Mode::Vector,
                _ => orbit_lance::Mode::Hybrid,
            };
            (vec![Box::new(ix.retriever(mode, &embedder))], ix.disk_bytes(), format!("LanceDB 0.40, {} rows, flat vector scan, inverted FTS index", ix.rows))
        }
        c => bail!("unknown config {c}"),
    };

    let (mut r1, mut r5, mut r10, mut mrr, mut ndcg) = (0.0, 0.0, 0.0, 0.0, 0.0);
    let mut lat = Vec::new();
    let mut details = Vec::new();
    for (q, v) in queries.iter().zip(&vecs) {
        let rel = relevant(store, q)?;
        let sq = SearchQuery { text: q.query.clone(), limit: 10, vector: Some(v.clone()), ..Default::default() };
        let (res, dt) = time(|| search_with(store, &retrievers, &sq));
        lat.push(dt);
        let res = res?;
        let top: Vec<String> = res.hits.iter().take(3).map(|h| format!("{} [{}]", h.path, h.kind)).collect();
        let hits: Vec<String> = res.hits.into_iter().filter(|h| h.corpus == Corpus::Chunk).map(|h| h.id).collect();
        let first = hits.iter().position(|h| rel.contains(h));
        details.push(json!({ "qid": q.qid, "split": q.split, "query": q.query, "want": q.path, "first": first, "top": top }));
        r1 += first.is_some_and(|p| p < 1) as u8 as f64;
        r5 += first.is_some_and(|p| p < 5) as u8 as f64;
        r10 += first.is_some_and(|p| p < 10) as u8 as f64;
        mrr += first.map_or(0.0, |p| 1.0 / (p + 1) as f64);
        let dcg: f64 = hits.iter().take(10).enumerate().filter(|(_, h)| rel.contains(*h)).map(|(i, _)| 1.0 / ((i + 2) as f64).log2()).sum();
        let idcg: f64 = (0..rel.len().min(10)).map(|i| 1.0 / ((i + 2) as f64).log2()).sum();
        ndcg += if idcg > 0.0 { dcg / idcg } else { 0.0 };
    }
    let n = queries.len() as f64;
    Ok(ConfigResult {
        config: rerank.map_or(config.to_string(), |m| format!("{config}+rerank:{m}")),
        queries: queries.len(),
        recall_at_1: r1 / n,
        recall_at_5: r5 / n,
        recall_at_10: r10 / n,
        mrr_at_10: mrr / n,
        ndcg_at_10: ndcg / n,
        latency: stats(lat),
        setup_ms: setup.as_secs_f64() * 1e3,
        peak_rss_mb: peak_rss_mb(),
        index_bytes,
        notes,
        details,
    })
}
