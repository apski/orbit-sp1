//! HNSW index (usearch) over `embeddings`, keyed by the stable embedding id, used
//! by the vector retriever once a model has `ORBIT_ANN_MIN` vectors (default 20k).
//! Updates are incremental: rows with an id above the last indexed one are added.
//! Candidates are re-scored exactly from SQLite, so the index only nominates.
use crate::sources::sha256;
use crate::storage::Store;
use anyhow::{anyhow, Result};
use std::path::PathBuf;
use usearch::{Index, IndexOptions, MetricKind, ScalarKind};

pub fn min_vectors() -> usize {
    std::env::var("ORBIT_ANN_MIN").ok().and_then(|v| v.parse().ok()).unwrap_or(20_000)
}

fn paths(store: &Store, model: &str) -> (PathBuf, PathBuf) {
    let base = store.dir().join("ann").join(&sha256(model)[..12]);
    (base.with_extension("usearch"), base.with_extension("max_id"))
}

fn options(dim: usize) -> IndexOptions {
    // bf16 halves the file; exact re-scoring from SQLite removes the rounding error
    IndexOptions { dimensions: dim, metric: MetricKind::IP, quantization: ScalarKind::BF16, ..Default::default() }
}

fn ex(e: impl std::fmt::Display) -> anyhow::Error {
    anyhow!("usearch: {e}")
}

/// Bring the on-disk index up to date, then memory-map it for search.
pub fn open(store: &Store, model: &str) -> Result<Index> {
    let (file, meta) = paths(store, model);
    let dim: usize = store.db.query_row("SELECT length(vec) / 4 FROM embeddings WHERE model = ?1 LIMIT 1", [model], |r| r.get(0))?;
    let max_db: i64 = store.db.query_row("SELECT COALESCE(MAX(id), 0) FROM embeddings WHERE model = ?1", [model], |r| r.get(0))?;
    // a restored (older) database can reuse ids the index already holds: start over
    let indexed: i64 = std::fs::read_to_string(&meta).ok().and_then(|s| s.trim().parse().ok()).filter(|&m| file.exists() && m <= max_db).unwrap_or(0);
    let path = file.to_str().ok_or_else(|| anyhow!("non-UTF-8 path"))?;
    if indexed < max_db {
        let ix = Index::new(&options(dim)).map_err(ex)?;
        if indexed > 0 {
            ix.load(path).map_err(ex)?;
        }
        let mut st = store.db.prepare("SELECT id, vec FROM embeddings WHERE model = ?1 AND id > ?2")?;
        let rows: Vec<(u64, Vec<f32>)> = st
            .query_map(rusqlite::params![model, indexed], |r| Ok((r.get::<_, i64>(0)? as u64, crate::embed::from_blob(r.get_ref(1)?.as_blob()?))))?
            .collect::<rusqlite::Result<_>>()?;
        ix.reserve(ix.size() + rows.len()).map_err(ex)?;
        for (id, v) in &rows {
            ix.add(*id, v).map_err(ex)?;
        }
        std::fs::create_dir_all(file.parent().unwrap())?;
        let tmp = file.with_extension(format!("tmp{}", std::process::id()));
        ix.save(tmp.to_str().unwrap()).map_err(ex)?;
        std::fs::rename(&tmp, &file)?; // atomic: a concurrent reader sees old or new
        std::fs::write(&meta, max_db.to_string())?;
    }
    let ix = Index::new(&options(dim)).map_err(ex)?;
    ix.view(path).map_err(ex)?;
    Ok(ix)
}

/// Embedding ids of the approximate `k` nearest vectors.
pub fn nearest(ix: &Index, q: &[f32], k: usize) -> Result<Vec<u64>> {
    Ok(ix.search(q, k).map_err(ex)?.keys)
}
