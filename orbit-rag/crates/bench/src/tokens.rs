//! Token-count accuracy: chars/4 and two BPE tokenizers against a local model's
//! own count (ollama `prompt_eval_count` for a raw prompt), on corpus chunks.
use crate::util::Rng;
use anyhow::Result;
use orbit_core::Store;
use serde::Serialize;
use serde_json::json;

#[derive(Serialize)]
pub struct Counter {
    pub name: String,
    /// sum(estimate) / sum(real)
    pub total_ratio: f64,
    pub mean_abs_err: f64,
    pub p95_abs_err: f64,
}

#[derive(Serialize)]
pub struct Report {
    pub reference: String,
    pub samples: usize,
    pub counters: Vec<Counter>,
}

fn real(model: &str, text: &str) -> Result<usize> {
    let url = std::env::var("OLLAMA_HOST").unwrap_or_else(|_| "http://127.0.0.1:11434".into());
    let v: serde_json::Value = ureq::post(&format!("{url}/api/generate"))
        .send_json(json!({ "model": model, "prompt": text, "raw": true, "stream": false, "options": { "num_predict": 1, "num_ctx": 16384 } }))?
        .into_json()?;
    Ok(v["prompt_eval_count"].as_u64().unwrap_or(0) as usize)
}

pub fn run(store: &Store, model: &str, n: usize) -> Result<Report> {
    let mut st = store.db.prepare("SELECT text FROM chunks WHERE length(text) > 200 ORDER BY id")?;
    let pool: Vec<String> = st.query_map([], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
    let mut rng = Rng::new(7);
    let texts: Vec<&String> = (0..n.min(pool.len())).map(|_| rng.pick(&pool)).collect();
    let offset = real(model, "a")?.saturating_sub(1); // BOS and friends
    let reals: Vec<usize> = texts.iter().map(|t| real(model, t).map(|r| r.saturating_sub(offset))).collect::<Result<_>>()?;
    let o200k = tiktoken_rs::o200k_base()?;
    let cl100k = tiktoken_rs::cl100k_base()?;
    let counters: [(&str, Box<dyn Fn(&str) -> usize>); 3] = [
        ("chars4", Box::new(|s: &str| s.len().div_ceil(4))),
        ("o200k", Box::new(|s: &str| o200k.encode_ordinary(s).len())),
        ("cl100k", Box::new(|s: &str| cl100k.encode_ordinary(s).len())),
    ];
    let counters = counters
        .iter()
        .map(|(name, f)| {
            let est: Vec<usize> = texts.iter().map(|t| f(t)).collect();
            let mut errs: Vec<f64> = est.iter().zip(&reals).map(|(e, r)| (*e as f64 - *r as f64).abs() / (*r).max(1) as f64).collect();
            errs.sort_by(f64::total_cmp);
            Counter {
                name: name.to_string(),
                total_ratio: est.iter().sum::<usize>() as f64 / reals.iter().sum::<usize>().max(1) as f64,
                mean_abs_err: errs.iter().sum::<f64>() / errs.len().max(1) as f64,
                p95_abs_err: errs.get((errs.len() as f64 * 0.95) as usize).copied().unwrap_or(0.0),
            }
        })
        .collect();
    Ok(Report { reference: model.into(), samples: texts.len(), counters })
}
