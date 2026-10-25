//! Vector-search scaling: synthetic unit vectors at several corpus sizes, the
//! same rows in every engine. Answers "where does brute force stop being enough".
//! Vectors are clustered (256 centroids plus noise), like real embeddings; uniform
//! random vectors are the worst case for HNSW and say little about real recall.
use crate::util::{stats, time, Rng, Stats};
use anyhow::Result;
use orbit_core::embed::{to_blob, Embedder};
use orbit_core::retrieval::{Retriever, SearchQuery, Vector};
use orbit_core::Store;
use rusqlite::params;
use serde::Serialize;

#[derive(Serialize)]
pub struct Point {
    pub n: usize,
    pub engine: String,
    pub latency: Stats,
    pub build_ms: f64,
    /// top-10 overlap with the exact scan (1.0 for exact engines)
    pub recall_at_10: f64,
    /// ANN only: time to add 1% new vectors to an existing index
    #[serde(skip_serializing_if = "Option::is_none")]
    pub incremental_ms: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub index_bytes: Option<u64>,
}

fn unit(rng: &mut Rng, d: usize) -> Vec<f32> {
    let mut v: Vec<f32> = (0..d).map(|_| (rng.next() % 2000) as f32 / 1000.0 - 1.0).collect();
    let n = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    v.iter_mut().for_each(|x| *x /= n);
    v
}

fn near(rng: &mut Rng, centroids: &[Vec<f32>]) -> Vec<f32> {
    let c = rng.pick(centroids);
    let noise = unit(rng, c.len());
    let mut v: Vec<f32> = c.iter().zip(&noise).map(|(a, b)| a + 0.7 * b).collect();
    let n = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    v.iter_mut().for_each(|x| *x /= n);
    v
}

pub fn run(sizes: &[usize], dim: usize, queries: usize) -> Result<Vec<Point>> {
    // the retrievers only need the model id; Hash embedder is never called (vectors are passed in)
    let model = Embedder::Hash;
    let mut out = Vec::new();
    for &n in sizes {
        let dir = std::env::temp_dir().join(format!("orbit-scale-{}-{n}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir)?;
        std::fs::write(dir.join("a.md"), "# a\n")?;
        let store = Store::init(&dir)?;
        let src = store.open_source(&dir.join("a.md"))?;
        let rev = store.latest_revision(&src)?.unwrap().id;
        let mut rng = Rng::new(n as u64);
        let centroids: Vec<Vec<f32>> = (0..256).map(|_| unit(&mut rng, dim)).collect();
        let tx = store.db.unchecked_transaction()?;
        store.db.execute("DELETE FROM chunks", [])?;
        for i in 0..n {
            let h = format!("h{i}");
            tx.execute("INSERT INTO chunks(revision_id, heading_path, scope, start_offset, end_offset, text, hash) VALUES (?1, '[]', '', 0, 1, ?2, ?3)", params![rev, format!("chunk {i}"), h])?;
            tx.execute("INSERT INTO embeddings(hash, model, vec) VALUES (?1, ?2, ?3)", params![h, model.id(), to_blob(&near(&mut rng, &centroids))])?;
        }
        tx.commit()?;
        let qs: Vec<SearchQuery> = (0..queries).map(|_| SearchQuery { text: "q".into(), limit: 10, vector: Some(near(&mut rng, &centroids)), ..Default::default() }).collect();
        let measure = |r: &dyn Retriever| -> Result<Stats> {
            r.search(&qs[0], 40)?;
            let d: Result<Vec<_>> = qs.iter().map(|q| { let (x, d) = time(|| r.search(q, 40)); x.map(|_| d) }).collect();
            Ok(stats(d?))
        };
        let top10 = |r: &dyn Retriever| -> Result<Vec<Vec<String>>> { qs.iter().map(|q| Ok(r.search(q, 10)?.into_iter().map(|c| c.id).collect())).collect() };
        #[cfg_attr(not(feature = "ann"), allow(unused_variables))]
        let recall = |got: &[Vec<String>], want: &[Vec<String>]| {
            got.iter().zip(want).map(|(g, w)| g.iter().filter(|x| w.contains(x)).count() as f64 / w.len().max(1) as f64).sum::<f64>() / want.len().max(1) as f64
        };
        std::env::set_var("ORBIT_ANN_MIN", usize::MAX.to_string()); // exact scan
        #[cfg_attr(not(feature = "ann"), allow(unused_variables))]
        let exact = top10(&Vector { store: &store, embedder: &model })?;
        let s = measure(&Vector { store: &store, embedder: &model })?;
        eprintln!("n={n:>7} sqlite-vector p50 {:.1} ms p95 {:.1} ms", s.p50_ms, s.p95_ms);
        out.push(Point { n, engine: "sqlite-vector".into(), latency: s, build_ms: 0.0, recall_at_10: 1.0, incremental_ms: None, index_bytes: None });
        #[cfg(feature = "ann")]
        {
            std::env::set_var("ORBIT_ANN_MIN", "0");
            let (ix, build) = time(|| orbit_core::ann::open(&store, &model.id()));
            drop(ix?);
            let v = Vector { store: &store, embedder: &model };
            let r = recall(&top10(&v)?, &exact);
            let s = measure(&v)?;
            let bytes = std::fs::read_dir(store.dir().join("ann"))?.filter_map(|e| e.ok()?.metadata().ok()).map(|m| m.len()).sum();
            // incremental: 1% new vectors, then the next open() adds just those
            let tx = store.db.unchecked_transaction()?;
            for i in n..n + n / 100 {
                tx.execute("INSERT INTO embeddings(hash, model, vec) VALUES (?1, ?2, ?3)", params![format!("h{i}"), model.id(), to_blob(&near(&mut rng, &centroids))])?;
            }
            tx.commit()?;
            let (ix, inc) = time(|| orbit_core::ann::open(&store, &model.id()));
            drop(ix?);
            eprintln!("n={n:>7} sqlite-ann    p50 {:.1} ms p95 {:.1} ms recall@10 {r:.3} (build {:.0} ms, +1% in {:.0} ms)", s.p50_ms, s.p95_ms, build.as_secs_f64() * 1e3, inc.as_secs_f64() * 1e3);
            out.push(Point { n, engine: "sqlite-ann".into(), latency: s, build_ms: build.as_secs_f64() * 1e3, recall_at_10: r, incremental_ms: Some(inc.as_secs_f64() * 1e3), index_bytes: Some(bytes) });
        }
        #[cfg(feature = "lance")]
        {
            let (ix, t) = time(|| orbit_lance::LanceIndex::open(&store, &model));
            let ix = ix?;
            let s = measure(&ix.retriever(orbit_lance::Mode::Vector, &model))?;
            eprintln!("n={n:>7} lance-vector  p50 {:.1} ms p95 {:.1} ms (build {:.0} ms, flat)", s.p50_ms, s.p95_ms, t.as_secs_f64() * 1e3);
            out.push(Point { n, engine: "lance-vector".into(), latency: s, build_ms: t.as_secs_f64() * 1e3, recall_at_10: 1.0, incremental_ms: None, index_bytes: None });
        }
        drop(store);
        let _ = std::fs::remove_dir_all(&dir);
    }
    Ok(out)
}
