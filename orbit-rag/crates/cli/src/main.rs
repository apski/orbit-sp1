mod mcp;
mod ops;
mod server;

use anyhow::{bail, Result};
use clap::{Parser, Subcommand};
use orbit_core::bundles::{bundle_with, cite, render_markdown};
use orbit_core::embed::Embedder;
use orbit_core::retrieval::{default_retrievers, search_with, Retrievers, SearchQuery};
use orbit_core::storage::{line_of, Record};
use orbit_core::Store;
use serde_json::json;
use std::path::{Path, PathBuf};

#[derive(Parser)]
#[command(name = "orbit-rag", about = "Local-first context engine: capture, re-anchor, retrieve, bundle")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Create .orbit-rag/ in the current directory (the registered root)
    Init,
    /// Re-read registered sources, re-anchor highlights, queue indexing
    Sync,
    /// Rebuild FTS/chunk/embedding indexes from canonical tables
    Reindex,
    /// Serve the viewer on 127.0.0.1 and open it
    Viewer {
        path: Option<PathBuf>,
        #[arg(long, default_value_t = 7420)]
        port: u16,
        #[arg(long)]
        no_open: bool,
    },
    /// Capture a highlight without the UI (agents, scripts)
    Capture {
        path: PathBuf,
        #[arg(long)]
        text: String,
        /// which occurrence of --text (1-based)
        #[arg(long, default_value_t = 1)]
        nth: usize,
        #[arg(long, default_value = "note")]
        category: String,
        #[arg(long)]
        note: Option<String>,
        #[arg(long)]
        scope: Option<String>,
        /// record as proposed (not human-approved)
        #[arg(long)]
        proposed: bool,
    },
    /// Register sources explicitly (Markdown or code: rs, ts/tsx, js/jsx, py)
    Add { paths: Vec<PathBuf> },
    /// Index git diff hunks between two revisions as a retrieval corpus
    DiffIndex {
        base: String,
        /// a revision, or "worktree" for uncommitted changes
        #[arg(long, default_value = "HEAD")]
        head: String,
    },
    /// Keep syncing sources and the embedding index; print re-anchor events
    Watch {
        #[arg(long, default_value_t = 1.0)]
        interval: f64,
    },
    /// Consistent snapshot of the store, zstd-compressed, in .orbit-rag/backups/
    Backup,
    /// Replace the store with a backup (the current one is kept as orbit.db.bak-<time>)
    Restore { file: PathBuf },
    /// Integrity checks: SQLite, foreign keys, snapshot hashes, derived index
    Check {
        /// instead: numeric constants in code that docs state differently
        #[arg(long)]
        drift: bool,
        #[arg(long)]
        json: bool,
    },
    /// Read-only MCP server on stdio
    Mcp,
    #[command(subcommand)]
    Context(Ctx),
}

#[derive(Subcommand)]
enum Ctx {
    Search {
        query: String,
        #[arg(long)]
        scope: Option<String>,
        #[arg(long, default_value_t = 10)]
        limit: usize,
        #[arg(long)]
        json: bool,
        /// include retired/superseded records
        #[arg(long)]
        all: bool,
        /// include code chunks, code diffs and highlights on code
        #[arg(long)]
        code: bool,
    },
    Show {
        id: String,
        #[arg(long)]
        json: bool,
    },
    /// Print the anchored source passage
    Source { id: String },
    Related {
        id: String,
        #[arg(long)]
        json: bool,
    },
    /// Add a typed edge: <src> <kind> <dst>
    Link {
        src: String,
        kind: String,
        dst: String,
        #[arg(long, default_value = "cli")]
        provenance: String,
    },
    #[command(subcommand)]
    Review(Review),
    Impact {
        path: PathBuf,
        #[arg(long, default_value_t = 3)]
        depth: usize,
        #[arg(long)]
        json: bool,
    },
    Bundle {
        #[arg(long)]
        task: String,
        #[arg(long)]
        scope: Option<String>,
        #[arg(long, default_value_t = 4000)]
        tokens: usize,
        #[arg(long, default_value = "markdown")]
        format: String,
        /// include code chunks, code diffs and highlights on code
        #[arg(long)]
        code: bool,
    },
    /// Offer each decision doc's decision passage as a proposed highlight, for one-step review
    Propose {
        #[arg(long)]
        dry_run: bool,
    },
    /// Machine-derived code model per subsystem: types, constants, config, API, boundaries
    Model {
        #[arg(long)]
        scope: Option<String>,
        #[arg(long)]
        json: bool,
    },
}

#[derive(Subcommand)]
enum Review {
    List {
        #[arg(long)]
        json: bool,
    },
    Show { id: String },
    Accept { id: String },
    Retire { id: String },
}

fn main() {
    if let Err(e) = run(Cli::parse()) {
        eprintln!("orbit-rag: {e:#}");
        std::process::exit(1);
    }
}

fn abs(p: &Path) -> Result<PathBuf> {
    Ok(if p.is_absolute() { p.to_path_buf() } else { std::env::current_dir()?.join(p) })
}

/// Sync sources and drain the embedding outbox before answering, so reads are fresh.
/// Embedding time per interactive command; `orbit-rag sync` and `reindex` drain the rest.
pub const INTERACTIVE_EMBED: std::time::Duration = std::time::Duration::from_secs(2);

fn open(quiet: bool) -> Result<(Store, Option<Embedder>)> {
    let store = Store::discover(&std::env::current_dir()?)?;
    let rep = store.sync_all()?;
    if !quiet {
        for (id, res) in &rep.reanchored {
            eprintln!("re-anchored {id}: {res}");
        }
        if !rep.flagged.is_empty() {
            eprintln!("needs review: {} (orbit-rag context review list)", rep.flagged.join(", "));
        }
    }
    let emb = Embedder::from_env()?;
    if let Some(e) = &emb {
        if let Err(err) = store.index_pending_within(e, Some(INTERACTIVE_EMBED)) {
            eprintln!("warning: embedding index not updated ({}): {err:#}", e.id());
        }
    }
    Ok((store, emb))
}

/// Retrieval engine, chosen by ORBIT_RETRIEVER: `sqlite` (default: FTS5 + vectors)
/// or `lance` (LanceDB hybrid; needs `--features lance`). Same contract either way.
pub struct Engine {
    #[cfg(feature = "lance")]
    lance: Option<orbit_lance::LanceIndex>,
}

pub fn engine(store: &Store, emb: Option<&Embedder>) -> Result<Engine> {
    match std::env::var("ORBIT_RERANK").as_deref() {
        Err(_) | Ok("none") | Ok("") => {}
        #[cfg(feature = "rerank")]
        Ok(m) => orbit_core::retrieval::set_reranker(Box::new(orbit_rerank::CrossEncoder::open(m)?)),
        #[cfg(not(feature = "rerank"))]
        Ok(_) => bail!("this orbit was built without the reranker (cargo build --features rerank)"),
    }
    let want = std::env::var("ORBIT_RETRIEVER").unwrap_or_else(|_| "sqlite".into());
    match (want.as_str(), emb) {
        ("sqlite", _) => Ok(Engine {
            #[cfg(feature = "lance")]
            lance: None,
        }),
        #[cfg(feature = "lance")]
        ("lance", Some(e)) => Ok(Engine { lance: Some(orbit_lance::LanceIndex::open(store, e)?) }),
        ("lance", None) => bail!("ORBIT_RETRIEVER=lance needs an embedder (ORBIT_EMBED)"),
        #[cfg(not(feature = "lance"))]
        ("lance", _) => {
            let _ = store;
            bail!("this orbit was built without LanceDB (cargo build --features lance)")
        }
        (w, _) => bail!("unknown ORBIT_RETRIEVER={w} (sqlite|lance)"),
    }
}

impl Engine {
    pub fn retrievers<'a>(&'a self, store: &'a Store, emb: Option<&'a Embedder>) -> Retrievers<'a> {
        #[cfg(feature = "lance")]
        if let (Some(ix), Some(e)) = (&self.lance, emb) {
            return vec![Box::new(ix.retriever(orbit_lance::Mode::Hybrid, e))];
        }
        default_retrievers(store, emb)
    }
}

/// The anchored passage with line numbers and the selection marked «like this».
pub fn source_passage(s: &Store, r: &Record) -> Result<String> {
    let text = s.snapshot(&r.snapshot_hash)?;
    let mut out = format!("{} {} · {}\n", r.uri(), r.revision_label(), record_line(r));
    let Some((st, en)) = r.start.zip(r.end) else {
        return Ok(out + &format!("(unresolved: {}; last known text: {:?})\n", r.resolution, r.exact));
    };
    let (l0, l1) = (line_of(&text, st), line_of(&text, en));
    let marked = format!("{}«{}»{}", &text[..st], &text[st..en], &text[en..]);
    for (i, line) in marked.lines().enumerate().skip(l0.saturating_sub(3)).take(l1 - l0 + 5) {
        out += &format!("{:>5} {} {line}\n", i + 1, if (l0..=l1).contains(&(i + 1)) { "▌" } else { " " });
    }
    Ok(out)
}

fn print_json(v: &impl serde::Serialize) -> Result<()> {
    println!("{}", serde_json::to_string_pretty(v)?);
    Ok(())
}

fn record_line(r: &Record) -> String {
    format!("[{}] {} · {} · {} · {}", r.id, r.category, r.validity(), r.authority, r.resolution)
}

fn run(cli: Cli) -> Result<()> {
    match cli.cmd {
        Cmd::Init => {
            let s = Store::init(&std::env::current_dir()?)?;
            println!("initialised {}", s.dir().display());
        }
        Cmd::Sync => {
            let (s, e) = open(false)?;
            let n: i64 = s.db.query_row("SELECT COUNT(*) FROM sources", [], |r| r.get(0))?;
            let k = e.as_ref().map(|e| s.index_pending(e)).transpose()?.unwrap_or(0);
            println!("{n} sources synced; {k} embeddings computed");
        }
        Cmd::Reindex => {
            let (s, e) = open(true)?;
            s.reindex()?;
            let n = e.as_ref().map(|e| s.index_pending(e)).transpose()?.unwrap_or(0);
            println!("reindexed; {n} embeddings computed");
        }
        Cmd::Viewer { path, port, no_open } => {
            let (s, _) = open(false)?;
            let rel = path.map(|p| s.rel_path(&abs(&p)?)).transpose()?;
            server::serve(s, port, rel, !no_open)?;
        }
        Cmd::Capture { path, text, nth, category, note, scope, proposed } => {
            let (s, e) = open(true)?;
            let src = s.open_source(&abs(&path)?)?;
            let rev = s.latest_revision(&src)?.unwrap();
            let content = s.snapshot(&rev.snapshot_hash)?;
            let Some((start, _)) = content.match_indices(&text).nth(nth - 1) else { bail!("occurrence {nth} of {text:?} not found in {}", rev.path) };
            let id = s.capture(&src, rev.id, start, start + text.len(), &category, note.as_deref(), scope.as_deref(), proposed)?;
            if let Some(e) = &e {
                let _ = s.index_pending_within(e, Some(INTERACTIVE_EMBED));
            }
            println!("{id}");
        }
        Cmd::Add { paths } => {
            let (s, e) = open(true)?;
            for p in paths {
                let id = s.open_source(&abs(&p)?)?;
                println!("{id} {}", s.rel_path(&abs(&p)?)?);
            }
            if let Some(e) = &e {
                let _ = s.index_pending_within(e, Some(INTERACTIVE_EMBED));
            }
        }
        Cmd::DiffIndex { base, head } => {
            let (s, e) = open(true)?;
            let n = s.index_diffs(&base, &head)?;
            if let Some(e) = &e {
                let _ = s.index_pending_within(e, Some(INTERACTIVE_EMBED));
            }
            println!("{n} hunks indexed for {base}..{head}");
        }
        Cmd::Watch { interval } => ops::watch(interval)?,
        Cmd::Backup => ops::backup(&Store::discover(&std::env::current_dir()?)?)?,
        Cmd::Restore { file } => ops::restore(&std::env::current_dir()?, &abs(&file)?)?,
        Cmd::Check { drift: false, .. } => ops::check(&Store::discover(&std::env::current_dir()?)?)?,
        Cmd::Check { drift: true, json } => ops::drift(&open(false)?.0, json)?,
        Cmd::Mcp => {
            let (s, e) = open(true)?;
            mcp::serve(s, e)?;
        }
        Cmd::Context(c) => context(c)?,
    }
    Ok(())
}

fn context(c: Ctx) -> Result<()> {
    let (s, e) = open(false)?;
    match c {
        Ctx::Search { query, scope, limit, json, all, code } => {
            let t = std::time::Instant::now();
            let eng = engine(&s, e.as_ref())?;
            let res = search_with(&s, &eng.retrievers(&s, e.as_ref()), &SearchQuery { text: query, scope, limit, include_inactive: all, include_code: code, vector: None })?;
            if json {
                return print_json(&res);
            }
            for w in &res.warnings {
                eprintln!("warning: {w}");
            }
            for (i, h) in res.hits.iter().enumerate() {
                let head = match &h.record {
                    Some(r) => record_line(r),
                    None if h.corpus == orbit_core::retrieval::Corpus::Diff => format!("[{}] diff hunk · {}", h.id, h.validity),
                    None => format!("[{}] source chunk · {}", h.id, h.validity),
                };
                println!("{:>2}. {head}  ({} {} match via {})", i + 1, h.kind, h.matched, h.retrievers.join("+"));
                let text: String = h.text.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(160).collect();
                println!("    {text}");
                println!("    {} · {}", cite(h), h.heading_path.join(" › "));
                if !h.code_refs.is_empty() {
                    println!("    implemented in: {}", h.code_refs.iter().map(|c| format!("{} {}:{}", c.name, c.path, c.line)).collect::<Vec<_>>().join(", "));
                }
            }
            eprintln!("{} hits in {:.1} ms ({})", res.hits.len(), t.elapsed().as_secs_f64() * 1e3, res.retrievers.join(", "));
        }
        Ctx::Show { id, json } => {
            let r = s.record(&id)?;
            let history: Vec<serde_json::Value> = s
                .db
                .prepare("SELECT a.id, a.resolution, a.source_revision_id, a.start_offset, a.end_offset, a.created_at FROM anchors a WHERE record_id = ?1 ORDER BY id")?
                .query_map([&id], |x| Ok(json!({"anchor": x.get::<_, i64>(0)?, "resolution": x.get::<_, String>(1)?, "revision": x.get::<_, i64>(2)?, "start": x.get::<_, Option<i64>>(3)?, "end": x.get::<_, Option<i64>>(4)?, "at": x.get::<_, String>(5)?})))?
                .collect::<rusqlite::Result<_>>()?;
            let edges = s.edges_of(&id)?;
            if json {
                return print_json(&json!({ "record": r, "validity": r.validity(), "anchor_history": history, "edges": edges }));
            }
            println!("{}", record_line(&r));
            println!("rev {} · scope {} · status {}", r.rev, r.scope, r.status);
            println!("\n  {}\n", r.body.replace('\n', "\n  "));
            if let Some(n) = &r.note {
                println!("note: {n}");
            }
            println!("source: {} ({}) {} · {}", r.uri(), r.source_id, r.revision_label(), r.heading_path.join(" › "));
            println!("anchor history:");
            for h in history {
                println!("  #{} {} rev {} {}..{} {}", h["anchor"], h["resolution"].as_str().unwrap_or(""), h["revision"], h["start"], h["end"], h["at"].as_str().unwrap_or(""));
            }
            for e in edges {
                println!("edge: {} {} {}", e.src, e.kind, e.dst);
            }
        }
        Ctx::Source { id } => print!("{}", source_passage(&s, &s.record(&id)?)?),
        Ctx::Related { id, json } => {
            let edges = s.edges_of(&id)?;
            if json {
                return print_json(&edges);
            }
            for e in edges {
                let other = if e.src == id { &e.dst } else { &e.src };
                let r = s.record(other)?;
                println!("{} {} {}  →  {}  {:?}", e.src, e.kind, e.dst, record_line(&r), r.body.chars().take(80).collect::<String>());
            }
        }
        Ctx::Link { src, kind, dst, provenance } => {
            s.link(&src, &kind, &dst, &provenance)?;
            println!("{src} {kind} {dst}");
        }
        Ctx::Review(r) => match r {
            Review::List { json } => {
                let items = s.review_list()?;
                if json {
                    return print_json(&items);
                }
                if items.is_empty() {
                    println!("review queue empty");
                }
                for i in items {
                    println!("{}  reason={}  {:?}", record_line(&i.record), i.reason, i.record.exact.chars().take(80).collect::<String>());
                }
            }
            Review::Show { id } => {
                let i = s.review_show(&id)?;
                println!("{}  reason={} opened {}", record_line(&i.record), i.reason, i.opened);
                if let Some((exact, rev)) = &i.approved {
                    println!("approved ({rev}):\n  {exact}");
                }
                match i.record.start {
                    Some(_) => println!("now ({}):\n  {}", i.record.revision_label(), i.record.exact),
                    None => println!("now: no position found"),
                }
                println!("\naccept: orbit-rag context review accept {id}   retire: orbit-rag context review retire {id}");
            }
            Review::Accept { id } => {
                s.review_accept(&id)?;
                println!("{id} accepted");
            }
            Review::Retire { id } => {
                s.retire(&id)?;
                println!("{id} retired");
            }
        },
        Ctx::Impact { path, depth, json } => {
            let rel = s.rel_path(&abs(&path)?)?;
            let Some(src) = s.source_by_path(&rel)? else { bail!("{rel} is not a registered source") };
            let imp = s.impact(&src, depth)?;
            if json {
                return print_json(&imp);
            }
            for i in imp {
                let indent = "  ".repeat(i.depth);
                let via = i.via.map(|v| format!("  (via {v})")).unwrap_or_default();
                println!("{indent}{}{via}", record_line(&i.record));
            }
        }
        Ctx::Propose { dry_run } => {
            let p = s.propose_decisions(dry_run)?;
            for (path, what) in &p {
                println!("{what}  {path}");
            }
            eprintln!("{} {} (review: orbit-rag context review list, or the viewer's Review tab)", p.len(), if dry_run { "would be proposed" } else { "proposed" });
        }
        Ctx::Model { scope, json } => {
            let m = orbit_core::model::code_model(&s, scope.as_deref())?;
            if json {
                return print_json(&m);
            }
            print!("{}", orbit_core::model::render_model(&m));
        }
        Ctx::Bundle { task, scope, tokens, format, code } => {
            let t = std::time::Instant::now();
            let eng = engine(&s, e.as_ref())?;
            let b = bundle_with(&s, &eng.retrievers(&s, e.as_ref()), &SearchQuery { text: task, scope, include_code: code, ..Default::default() }, tokens)?;
            match format.as_str() {
                "json" => print_json(&b)?,
                "markdown" | "md" => print!("{}", render_markdown(&b)),
                f => bail!("unknown format {f} (markdown|json)"),
            }
            eprintln!("bundle {} · {} items · ~{} tokens · {:.1} ms", b.bundle_id, b.items.len(), b.used, t.elapsed().as_secs_f64() * 1e3);
        }
    }
    Ok(())
}

