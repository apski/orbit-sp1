//! Embedding providers, selected with ORBIT_EMBED:
//!   ollama:<model>   local (default: ollama:nomic-embed-text)
//!   openai:<model>   hosted, optional, needs OPENAI_API_KEY
//!   hash             deterministic feature hashing; offline tests only
//!   none             lexical retrieval only
use crate::storage::Store;
use anyhow::{bail, Result};
use rusqlite::params;
use serde_json::{json, Value};
use std::time::Duration;

#[derive(Clone, Copy, PartialEq)]
pub enum Role {
    Document,
    Query,
}

pub enum Embedder {
    Ollama { url: String, model: String },
    OpenAi { model: String, key: String },
    Hash,
}

impl Embedder {
    pub fn from_env() -> Result<Option<Embedder>> {
        let spec = std::env::var("ORBIT_EMBED").unwrap_or_else(|_| "ollama:nomic-embed-text".into());
        Ok(match spec.split_once(':').unwrap_or((spec.as_str(), "")) {
            ("none", _) => None,
            ("hash", _) => Some(Embedder::Hash),
            ("ollama", m) => Some(Embedder::Ollama {
                url: std::env::var("OLLAMA_HOST").unwrap_or_else(|_| "http://127.0.0.1:11434".into()),
                model: m.into(),
            }),
            ("openai", m) => Some(Embedder::OpenAi { model: m.into(), key: std::env::var("OPENAI_API_KEY")? }),
            _ => bail!("bad ORBIT_EMBED={spec}"),
        })
    }

    /// Cache key: provider/model plus the input-format version.
    pub fn id(&self) -> String {
        match self {
            Embedder::Ollama { model, .. } => format!("ollama/{model}@v1"),
            Embedder::OpenAi { model, .. } => format!("openai/{model}@v1"),
            Embedder::Hash => "hash/fnv384@v1".into(),
        }
    }

    pub fn embed(&self, texts: &[String], role: Role) -> Result<Vec<Vec<f32>>> {
        let mut out: Vec<Vec<f32>> = match self {
            Embedder::Ollama { url, model } => {
                // nomic-style models want task prefixes; harmless for others
                let p = if role == Role::Query { "search_query: " } else { "search_document: " };
                let input: Vec<String> = texts.iter().map(|t| format!("{p}{t}")).collect();
                let v: Value = ureq::post(&format!("{url}/api/embed"))
                    .timeout(Duration::from_secs(120))
                    .send_json(json!({ "model": model, "input": input }))?
                    .into_json()?;
                serde_json::from_value(v["embeddings"].clone())?
            }
            Embedder::OpenAi { model, key } => {
                let v: Value = ureq::post("https://api.openai.com/v1/embeddings")
                    .set("Authorization", &format!("Bearer {key}"))
                    .timeout(Duration::from_secs(60))
                    .send_json(json!({ "model": model, "input": texts }))?
                    .into_json()?;
                v["data"].as_array().unwrap_or(&vec![]).iter().map(|d| serde_json::from_value(d["embedding"].clone())).collect::<Result<_, _>>()?
            }
            Embedder::Hash => texts.iter().map(|t| hash_embed(t)).collect(),
        };
        if out.len() != texts.len() {
            bail!("embedder returned {} vectors for {} inputs", out.len(), texts.len());
        }
        out.iter_mut().for_each(|v| normalize(v));
        Ok(out)
    }
}

fn normalize(v: &mut [f32]) {
    let n = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    if n > 0.0 {
        v.iter_mut().for_each(|x| *x /= n);
    }
}

fn hash_embed(t: &str) -> Vec<f32> {
    let mut v = vec![0f32; 384];
    let lower = t.to_lowercase();
    for w in lower.split(|c: char| !c.is_alphanumeric()).filter(|w| w.len() > 1) {
        let h = w.bytes().fold(0xcbf29ce484222325u64, |h, b| (h ^ b as u64).wrapping_mul(0x100000001b3));
        v[(h % 384) as usize] += if h & 1 << 63 == 0 { 1.0 } else { -1.0 };
    }
    v
}

pub fn to_blob(v: &[f32]) -> Vec<u8> {
    v.iter().flat_map(|x| x.to_le_bytes()).collect()
}

pub fn from_blob(b: &[u8]) -> Vec<f32> {
    b.as_chunks::<4>().0.iter().map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]])).collect()
}

impl Store {
    /// Drain the embedding outbox. Content stays queued if the provider is down,
    /// so a committed record can never silently miss its vector.
    pub fn index_pending(&self, e: &Embedder) -> Result<usize> {
        self.index_pending_within(e, None)
    }

    /// Like `index_pending`, but stops starting new batches after `budget`, so an
    /// interactive command (or an MCP call) never waits on a large backlog.
    pub fn index_pending_within(&self, e: &Embedder, budget: Option<std::time::Duration>) -> Result<usize> {
        let t = std::time::Instant::now();
        let model = e.id();
        let mut done = 0;
        while budget.is_none_or(|b| t.elapsed() < b) {
            let batch: Vec<(String, String)> = self
                .db
                .prepare("SELECT hash, text FROM embed_outbox o WHERE NOT EXISTS (SELECT 1 FROM embeddings WHERE hash = o.hash AND model = ?1) LIMIT 32")?
                .query_map([&model], |r| Ok((r.get(0)?, r.get(1)?)))?
                .collect::<rusqlite::Result<_>>()?;
            if batch.is_empty() {
                break;
            }
            let vecs = e.embed(&batch.iter().map(|b| b.1.clone()).collect::<Vec<_>>(), Role::Document)?;
            let tx = self.db.unchecked_transaction()?;
            for ((h, _), v) in batch.iter().zip(vecs) {
                tx.execute("INSERT INTO embeddings(hash, model, vec) VALUES (?1, ?2, ?3) ON CONFLICT(hash, model) DO UPDATE SET vec = excluded.vec", params![h, model, to_blob(&v)])?;
            }
            tx.commit()?;
            done += batch.len();
        }
        // whatever is embedded for this model has left the outbox
        self.db.execute("DELETE FROM embed_outbox WHERE hash IN (SELECT hash FROM embeddings WHERE model = ?1)", [&model])?;
        Ok(done)
    }

    /// Rebuild the derived index (FTS rows, chunk rows, outbox) from canonical tables.
    pub fn reindex(&self) -> Result<()> {
        let tx = self.db.unchecked_transaction()?;
        let ids: Vec<String> = self.db.prepare("SELECT id FROM sources")?.query_map([], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
        for id in ids {
            if let Some(rev) = self.latest_revision(&id)? {
                self.reindex_chunks(&id, rev.id, &rev.path, &self.snapshot(&rev.snapshot_hash)?)?;
            }
        }
        let recs: Vec<String> = self.db.prepare("SELECT id FROM records")?.query_map([], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
        for id in recs {
            self.index_record(&id)?;
        }
        tx.commit()?;
        Ok(())
    }
}
