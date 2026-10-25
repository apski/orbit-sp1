mod agent;
mod anchors;
mod drift;
mod perf;
mod retrieval;
mod scale;
mod tokens;
mod util;

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "orbit-bench", about = "orbit evaluation: anchoring, retrieval, latency gates, agent trials")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Seeded source-edit fixtures; fails if any confident attachment is wrong
    Anchors {
        #[arg(long, default_value = "benchmarks/corpus/book/src")]
        corpus: PathBuf,
        #[arg(long, default_value_t = 2000)]
        cases: usize,
        #[arg(long, default_value_t = 1)]
        seed: u64,
    },
    /// Index the corpus (register sources, embed)
    Setup {
        #[arg(long, default_value = "benchmarks/corpus/book/src")]
        corpus: PathBuf,
    },
    /// Generate labelled queries with a local LLM (doc2query)
    GenQueries {
        #[arg(long, default_value = "benchmarks/corpus/book/src")]
        corpus: PathBuf,
        #[arg(long, default_value_t = 120)]
        n: usize,
        #[arg(long, default_value = "gemma4:26b")]
        model: String,
        #[arg(long, default_value_t = 42)]
        seed: u64,
        #[arg(long, default_value = "benchmarks/queries.jsonl")]
        out: PathBuf,
        /// only passages from paths starting with one of these (comma-separated)
        #[arg(long, value_delimiter = ',')]
        include: Vec<String>,
        /// use the corpus's existing .orbit-rag store as is (no registering, no embedding)
        #[arg(long)]
        existing: bool,
        /// markdown chunks only (skip code)
        #[arg(long)]
        docs_only: bool,
    },
    /// Score one retrieval config (fts5, sqlite-vector, sqlite-hybrid, lance-fts, lance-vector, lance-hybrid)
    Retrieval {
        config: String,
        #[arg(long, default_value = "benchmarks/corpus/book/src")]
        corpus: PathBuf,
        #[arg(long, default_value = "benchmarks/queries.jsonl")]
        queries: PathBuf,
        /// use the corpus's existing .orbit-rag store as is (no registering, no embedding)
        #[arg(long)]
        existing: bool,
        /// only queries whose `split` is this (e.g. tune, held)
        #[arg(long)]
        split: Option<String>,
    },
    /// Latency gates (warm in-process, cold CLI processes)
    Perf {
        #[arg(long, default_value = "benchmarks/corpus/book/src")]
        corpus: PathBuf,
        #[arg(long, default_value = "benchmarks/queries.jsonl")]
        queries: PathBuf,
        #[arg(long, default_value_t = 200)]
        iters: usize,
    },
    /// Vector search latency vs corpus size (synthetic vectors), both engines
    Scale {
        #[arg(long, value_delimiter = ',', default_value = "10000,50000,100000,200000")]
        sizes: Vec<usize>,
        #[arg(long, default_value_t = 768)]
        dim: usize,
        #[arg(long, default_value_t = 30)]
        queries: usize,
    },
    /// Code-constant vs docs drift checker against hand-labelled pairs
    Drift {
        #[arg(long, default_value = "fixtures/agent-repo")]
        docs: PathBuf,
        #[arg(long, default_value = "fixtures/tidepool-src")]
        code: PathBuf,
        #[arg(long, default_value = "benchmarks/drift_expected.json")]
        expected: PathBuf,
    },
    /// Token-count accuracy of chars/4, o200k and cl100k against a local model's count
    Tokens {
        #[arg(long, default_value = "benchmarks/corpus/book/src")]
        corpus: PathBuf,
        #[arg(long, default_value = "gemma3:4b")]
        model: String,
        #[arg(long, default_value_t = 300)]
        n: usize,
    },
    /// No context vs full docs vs raw RAG vs orbit bundle, repeated trials
    Agent {
        #[arg(long, default_value = "fixtures/agent-repo")]
        fixture: PathBuf,
        #[arg(long, default_value = "benchmarks/agent_tasks.json")]
        tasks: PathBuf,
        #[arg(long, default_value = "gemma4:26b")]
        model: String,
        #[arg(long, default_value_t = 3)]
        trials: usize,
        #[arg(long, default_value_t = 1200)]
        budget: usize,
        /// write each task's contexts to this directory and skip the model
        #[arg(long)]
        dump: Option<PathBuf>,
    },
}

fn load_queries(p: &PathBuf) -> Result<Vec<retrieval::Query>> {
    let text = std::fs::read_to_string(p).with_context(|| format!("{} (run gen-queries)", p.display()))?;
    let mut qs: Vec<retrieval::Query> = text.lines().filter(|l| !l.trim().is_empty()).map(serde_json::from_str).collect::<Result<_, _>>()?;
    qs.iter_mut().enumerate().for_each(|(i, q)| q.qid = i);
    Ok(qs)
}

fn main() -> Result<()> {
    match Cli::parse().cmd {
        Cmd::Anchors { corpus, cases, seed } => {
            let r = anchors::run(&corpus, cases, seed)?;
            out(&r);
            anyhow::ensure!(r.total.false_confident == 0, "{} confident attachments were wrong", r.total.false_confident);
        }
        Cmd::Setup { corpus } => {
            let s = retrieval::setup(&corpus)?;
            let n: i64 = s.db.query_row("SELECT COUNT(*) FROM chunks", [], |r| r.get(0))?;
            eprintln!("{} sources, {n} chunks", s.markdown_files()?.len());
        }
        Cmd::GenQueries { corpus, n, model, seed, out: path, include, existing, docs_only } => {
            let s = if existing { orbit_core::Store::discover(&corpus)? } else { retrieval::setup(&corpus)? };
            let qs = retrieval::gen_queries(&s, n, &model, seed, &include, docs_only)?;
            std::fs::write(&path, qs.iter().map(|q| serde_json::to_string(q).unwrap() + "\n").collect::<String>())?;
            eprintln!("wrote {} queries to {}", qs.len(), path.display());
        }
        Cmd::Retrieval { config, corpus, queries, existing, split } => {
            let s = if existing { orbit_core::Store::discover(&corpus)? } else { retrieval::setup(&corpus)? };
            let qs: Vec<_> = load_queries(&queries)?.into_iter().filter(|q| split.as_ref().is_none_or(|sp| q.split.as_deref() == Some(sp.as_str()))).collect();
            out(&retrieval::evaluate(&s, &config, &qs)?);
        }
        Cmd::Perf { corpus, queries, iters } => {
            retrieval::setup(&corpus)?;
            out(&perf::run(&corpus, &load_queries(&queries)?, &perf::orbit_bin(), iters)?);
        }
        Cmd::Drift { docs, code, expected } => out(&drift::run(&docs, &code, &expected)?),
        Cmd::Tokens { corpus, model, n } => out(&tokens::run(&retrieval::setup(&corpus)?, &model, n)?),
        Cmd::Scale { sizes, dim, queries } => out(&scale::run(&sizes, dim, queries)?),
        Cmd::Agent { fixture, tasks, model, trials, budget, dump } => {
            let tasks: Vec<agent::Task> = serde_json::from_str(&std::fs::read_to_string(tasks)?)?;
            if let Some(d) = dump {
                return agent::dump(&fixture, &tasks, budget, &d);
            }
            out(&agent::run(&fixture, &tasks, &model, trials, budget)?);
        }
    }
    Ok(())
}

fn out(v: &impl serde::Serialize) {
    println!("{}", serde_json::to_string_pretty(v).unwrap());
}
