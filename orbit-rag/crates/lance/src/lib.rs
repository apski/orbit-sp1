//! LanceDB retrieval adapter. LanceDB holds a *rebuildable mirror* of SQLite's
//! derived index (chunks, records, diff hunks + their embeddings) and only
//! nominates ids; validity, scope and hydration stay in SQLite.
use anyhow::{Context, Result};
use arrow_array::{Array, FixedSizeListArray, Float32Array, RecordBatch, StringArray};
use arrow_schema::{DataType, Field, Schema};
use futures::TryStreamExt;
use lancedb::database::CreateTableMode;
use lancedb::index::scalar::{FtsIndexBuilder, FullTextSearchQuery};
use lancedb::index::Index;
use lancedb::query::{ExecutableQuery, QueryBase, Select};
use lancedb::DistanceType;
use orbit_core::embed::{from_blob, Embedder, Role};
use orbit_core::retrieval::{rank_joint, Candidate, Corpus, Retriever, SearchQuery};
use orbit_core::Store;
use std::path::PathBuf;
use std::sync::Arc;

const TABLE: &str = "items";

pub struct LanceIndex {
    rt: tokio::runtime::Runtime,
    table: lancedb::Table,
    pub dir: PathBuf,
    pub rows: usize,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Mode {
    Lexical,
    Vector,
    Hybrid,
}

/// Anything that changes the derived index emits an event or an embedding row.
fn generation(store: &Store, model: &str) -> Result<String> {
    let (ev, emb): (i64, i64) = store.db.query_row(
        "SELECT (SELECT COALESCE(MAX(id), 0) FROM events), (SELECT COUNT(*) FROM embeddings WHERE model = ?1)",
        [model],
        |r| Ok((r.get(0)?, r.get(1)?)),
    )?;
    Ok(format!("{ev}:{emb}:{model}"))
}

impl LanceIndex {
    /// Open the mirror, rebuilding it when SQLite moved on since the last build.
    // ponytail: full rebuild on any change; switch to merge_insert by content hash
    // when corpora outgrow a sub-second rebuild.
    pub fn open(store: &Store, embedder: &Embedder) -> Result<LanceIndex> {
        let rt = tokio::runtime::Builder::new_multi_thread().enable_all().build()?;
        let dir = store.dir().join("lance");
        let model = embedder.id();
        let gen = generation(store, &model)?;
        let stamp = dir.join("GENERATION");
        let db = rt.block_on(lancedb::connect(dir.to_str().context("utf8 path")?).execute())?;
        let fresh = std::fs::read_to_string(&stamp).ok().as_deref() == Some(gen.as_str());
        let table = if fresh {
            rt.block_on(db.open_table(TABLE).execute())?
        } else {
            let batch = Self::batch(store, &model)?;
            let t = rt.block_on(db.create_table(TABLE, batch).mode(CreateTableMode::Overwrite).execute())?;
            rt.block_on(t.create_index(&["text"], Index::FTS(FtsIndexBuilder::default())).execute())?;
            std::fs::write(&stamp, &gen)?;
            t
        };
        let rows = rt.block_on(table.count_rows(None))?;
        Ok(LanceIndex { rt, table, dir, rows })
    }

    fn batch(store: &Store, model: &str) -> Result<RecordBatch> {
        let mut st = store.db.prepare(
            "SELECT 'chunk', CAST(c.id AS TEXT), c.heading_path || ' ' || c.text, e.vec
               FROM chunks c JOIN embeddings e ON e.hash = c.hash AND e.model = ?1
             UNION ALL
             SELECT 'record', f.record_id, f.heading || ' ' || f.body || ' ' || COALESCE(f.note, ''), e.vec
               FROM records_fts f JOIN record_index ri ON ri.record_id = f.record_id JOIN embeddings e ON e.hash = ri.hash AND e.model = ?1
             UNION ALL
             SELECT 'diff', CAST(d.id AS TEXT), d.path || ' ' || d.text, e.vec
               FROM diff_hunks d JOIN embeddings e ON e.hash = d.hash AND e.model = ?1",
        )?;
        let rows: Vec<(String, String, String, Vec<f32>)> = st
            .query_map([model], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, from_blob(&r.get::<_, Vec<u8>>(3)?))))?
            .collect::<rusqlite::Result<_>>()?;
        anyhow::ensure!(!rows.is_empty(), "nothing embedded for {model} yet (run `orbit-rag reindex`)");
        let dim = rows[0].3.len() as i32;
        let item = Arc::new(Field::new("item", DataType::Float32, true));
        let schema = Arc::new(Schema::new(vec![
            Field::new("corpus", DataType::Utf8, false),
            Field::new("id", DataType::Utf8, false),
            Field::new("text", DataType::Utf8, false),
            Field::new("vector", DataType::FixedSizeList(item.clone(), dim), false),
        ]));
        let flat = Float32Array::from_iter_values(rows.iter().flat_map(|r| r.3.iter().copied()));
        let vectors = FixedSizeListArray::try_new(item, dim, Arc::new(flat), None)?;
        let col = |f: fn(&(String, String, String, Vec<f32>)) -> &str| Arc::new(StringArray::from_iter_values(rows.iter().map(f))) as Arc<dyn Array>;
        Ok(RecordBatch::try_new(schema, vec![col(|r| &r.0), col(|r| &r.1), col(|r| &r.2), Arc::new(vectors)])?)
    }

    pub fn retriever<'a>(&'a self, mode: Mode, embedder: &'a Embedder) -> LanceRetriever<'a> {
        LanceRetriever { index: self, mode, embedder }
    }

    pub fn disk_bytes(&self) -> u64 {
        fn du(p: &std::path::Path) -> u64 {
            std::fs::read_dir(p).map_or(0, |rd| {
                rd.flatten().map(|e| if e.path().is_dir() { du(&e.path()) } else { e.metadata().map_or(0, |m| m.len()) }).sum()
            })
        }
        du(&self.dir)
    }
}

pub struct LanceRetriever<'a> {
    index: &'a LanceIndex,
    mode: Mode,
    embedder: &'a Embedder,
}

impl Retriever for LanceRetriever<'_> {
    fn name(&self) -> String {
        format!("lance-{}", format!("{:?}", self.mode).to_lowercase())
    }

    fn search(&self, q: &SearchQuery, k: usize) -> Result<Vec<Candidate>> {
        let ix = self.index;
        let vector = || -> Result<Vec<f32>> {
            match &q.vector {
                Some(v) => Ok(v.clone()),
                None => Ok(self.embedder.embed(std::slice::from_ref(&q.text), Role::Query)?.remove(0)),
            }
        };
        let fts = || FullTextSearchQuery::new(q.text.clone());
        let select = Select::columns(&["corpus", "id"]);
        let fetch = k * 3;
        let batches: Vec<RecordBatch> = ix.rt.block_on(async {
            let stream = match self.mode {
                Mode::Lexical => ix.table.query().full_text_search(fts()).select(select).limit(fetch).execute().await?,
                Mode::Vector => ix.table.query().nearest_to(vector()?)?.distance_type(DistanceType::Cosine).select(select).limit(fetch).execute().await?,
                Mode::Hybrid => ix.table.query().full_text_search(fts()).nearest_to(vector()?)?.distance_type(DistanceType::Cosine).select(select).limit(fetch).execute().await?,
            };
            anyhow::Ok(stream.try_collect().await?)
        })?;
        let mut rows: Vec<(f32, Corpus, String)> = Vec::new();
        for b in &batches {
            let s = |n: &str| b.column_by_name(n).and_then(|c| c.as_any().downcast_ref::<StringArray>().cloned());
            let (Some(corpus), Some(id)) = (s("corpus"), s("id")) else { continue };
            // best-first key: hybrid -> _relevance_score desc, fts -> _score desc, vector -> _distance asc
            let score = ["_relevance_score", "_score", "_distance"].iter().find_map(|n| {
                b.column_by_name(n).and_then(|c| c.as_any().downcast_ref::<Float32Array>().cloned()).map(|a| (*n, a))
            });
            for i in 0..b.num_rows() {
                let key = match &score {
                    Some(("_distance", a)) => -a.value(i),
                    Some((_, a)) => a.value(i),
                    None => -(rows.len() as f32),
                };
                let c = match corpus.value(i) {
                    "record" => Corpus::Record,
                    "diff" => Corpus::Diff,
                    _ => Corpus::Chunk,
                };
                rows.push((key, c, id.value(i).to_string()));
            }
        }
        rows.sort_by(|a, b| b.0.total_cmp(&a.0));
        Ok(rank_joint(rows.into_iter().map(|r| (r.1, r.2)), k))
    }
}

