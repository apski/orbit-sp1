//! Cross-encoder reranker (fastembed, ONNX on CPU). The model downloads from
//! Hugging Face on first use into `~/.cache/orbit/fastembed`.
use anyhow::{anyhow, Result};
use fastembed::{RerankInitOptions, RerankerModel, TextRerank};
use orbit_core::retrieval::Reranker;
use std::sync::Mutex;

pub struct CrossEncoder {
    name: String,
    model: Mutex<TextRerank>,
}

impl CrossEncoder {
    /// `jina-turbo` (38M params, English) or `bge-base` (278M, multilingual).
    pub fn open(which: &str) -> Result<CrossEncoder> {
        let model = match which {
            "jina-turbo" => RerankerModel::JINARerankerV1TurboEn,
            "bge-base" => RerankerModel::BGERerankerBase,
            m => return Err(anyhow!("unknown reranker {m} (jina-turbo, bge-base)")),
        };
        let cache = std::path::PathBuf::from(std::env::var("HOME")?).join(".cache/orbit/fastembed");
        // 512 tokens covers a chunk; the model default (8k) pads every batch to it
        let opts = RerankInitOptions::new(model).with_cache_dir(cache).with_max_length(512).with_show_download_progress(false);
        Ok(CrossEncoder { name: format!("rerank({which})"), model: Mutex::new(TextRerank::try_new(opts)?) })
    }
}

impl Reranker for CrossEncoder {
    fn name(&self) -> String {
        self.name.clone()
    }
    fn scores(&self, query: &str, docs: &[&str]) -> Result<Vec<f32>> {
        let res = self.model.lock().map_err(|_| anyhow!("reranker poisoned"))?.rerank(query, docs, false, None)?;
        let mut out = vec![f32::MIN; docs.len()];
        for r in res {
            out[r.index] = r.score;
        }
        Ok(out)
    }
}
