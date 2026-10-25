//! scope -> candidate retrievers (lexical, vector) -> RRF -> validation -> dedupe -> optional rerank.
//! Retrievers only nominate; selection happens in `search`/bundles.
//! SQLite FTS5 + brute-force vectors is the spike baseline from the plan; a
//! LanceDB retriever slots in by implementing `Retriever`.
use crate::embed::{Embedder, Role};
use crate::storage::{line_of, Record, Store};
use anyhow::Result;
use serde::Serialize;
use std::collections::{HashMap, HashSet};

#[derive(Clone, Debug, Default)]
pub struct SearchQuery {
    pub text: String,
    pub scope: Option<String>,
    pub limit: usize,
    pub include_inactive: bool,
    /// Precomputed query embedding (benchmarks exclude inference from timings).
    pub vector: Option<Vec<f32>>,
    /// Code chunks, code diffs and highlights on code are left out unless set.
    pub include_code: bool,
}

/// Declaration order is the tie-break order: curated > current source > history.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Corpus {
    Record,
    Chunk,
    Diff,
}

#[derive(Clone, Debug)]
pub struct Candidate {
    pub corpus: Corpus,
    pub id: String,
    pub rank: usize, // 0-based, across all corpora of this retriever
}

pub trait Retriever {
    fn name(&self) -> String;
    fn search(&self, q: &SearchQuery, k: usize) -> Result<Vec<Candidate>>;
}

/// Second-stage scorer (a cross-encoder) over the fused, validated hits.
pub trait Reranker: Send + Sync {
    fn name(&self) -> String;
    /// One score per document, higher = more relevant.
    fn scores(&self, query: &str, docs: &[&str]) -> Result<Vec<f32>>;
}

static RERANKER: std::sync::OnceLock<Box<dyn Reranker>> = std::sync::OnceLock::new();

/// Process-wide reranker (the CLI sets it from `ORBIT_RERANK`); first call wins.
pub fn set_reranker(r: Box<dyn Reranker>) {
    let _ = RERANKER.set(r);
}

/// Hits the reranker sees: deeper than the result so it can promote from below.
const RERANK_DEPTH: usize = 30;
/// bm25 weight of the heading-path column relative to body text (`ORBIT_HEADING_WEIGHT`).
/// 3.0 was tried and did not help on the held-out eval (RESULTS.md §8), so it is off.
const HEADING_WEIGHT: f64 = 1.0;
/// Doc chunks with fewer prose words than this (links and headings removed) are thin.
const THIN_WORDS: usize = 6;
const THIN_WEIGHT: f64 = 0.5;

/// Words of prose, ignoring `[[wiki links]]`, URLs and heading lines.
fn prose_words(text: &str) -> usize {
    let mut out = String::new();
    let mut rest = text;
    while let Some(i) = rest.find("[[") {
        out.push_str(&rest[..i]);
        rest = rest[i..].find("]]").map_or("", |j| &rest[i + j + 2..]);
    }
    out.push_str(rest);
    out.lines()
        .filter(|l| !l.trim_start().starts_with('#'))
        .flat_map(|l| l.split_whitespace())
        .filter(|w| !w.contains("://") && w.chars().any(char::is_alphabetic))
        .count()
}

/// Below this many exact-word candidates, the lexical retriever also tries word prefixes.
const PREFIX_FALLBACK: usize = 5;
/// Candidates the kind prior re-orders, as a multiple of the requested limit.
const PRIOR_DEPTH: usize = 3;

pub struct Lexical<'a>(pub &'a Store);

impl Retriever for Lexical<'_> {
    fn name(&self) -> String {
        "fts5".into()
    }
    fn search(&self, q: &SearchQuery, k: usize) -> Result<Vec<Candidate>> {
        let words: Vec<&str> = q.text.split(|c: char| !c.is_alphanumeric()).filter(|t| t.len() > 1).collect();
        if words.is_empty() {
            return Ok(vec![]);
        }
        // exact words first; prefixes ("fire" -> "Firebase") only when that finds almost
        // nothing: prefix terms on every query reshuffled tight bundles (RESULTS.md §8)
        let exact = |t: &&str| format!("\"{t}\"");
        let mut scored = self.query(q, k, &words.iter().map(exact).collect::<Vec<_>>().join(" OR "))?;
        if scored.len() < PREFIX_FALLBACK && std::env::var("ORBIT_FTS_PREFIX").as_deref() != Ok("0") {
            let pre = words.iter().map(|t| if t.chars().count() >= 3 { format!("\"{t}\"*") } else { exact(t) }).collect::<Vec<_>>().join(" OR ");
            let found: Vec<_> = self.query(q, k, &pre)?.into_iter().filter(|x| !scored.iter().any(|y| y.1 == x.1 && y.2 == x.2)).collect();
            scored.extend(found); // after the exact hits, which keep their order
            return Ok(rank_joint(scored.into_iter().map(|(_, c, id)| (c, id)), k));
        }
        scored.sort_by(|a, b| a.0.total_cmp(&b.0));
        Ok(rank_joint(scored.into_iter().map(|(_, c, id)| (c, id)), k))
    }
}

impl Lexical<'_> {
    /// One bm25-ordered list (lower = better) across records, chunks and diffs for an FTS5 expression.
    fn query(&self, q: &SearchQuery, k: usize, m: &str) -> Result<Vec<(f64, Corpus, String)>> {
        // a match in the heading path counts more than one in body text
        let hw: f64 = std::env::var("ORBIT_HEADING_WEIGHT").ok().and_then(|v| v.parse().ok()).unwrap_or(HEADING_WEIGHT);
        // one bm25-ordered list across the three tables (lower bm25 = better)
        let mut scored: Vec<(f64, Corpus, String)> = Vec::new();
        for (corpus, sql) in [
            (Corpus::Record, "SELECT record_id, bm25(records_fts, 0.0, ?4, 1.0, 1.0) FROM records_fts WHERE records_fts MATCH ?1 AND ?3 ORDER BY 2 LIMIT ?2"),
            (
                Corpus::Chunk,
                "SELECT CAST(chunks_fts.chunk_id AS TEXT), bm25(chunks_fts, 0.0, ?4, 1.0) FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.chunk_id
                 WHERE chunks_fts MATCH ?1 AND (?3 OR c.lang = 'markdown') ORDER BY 2 LIMIT ?2",
            ),
            (
                Corpus::Diff,
                "SELECT CAST(diffs_fts.hunk_id AS TEXT), bm25(diffs_fts, 0.0, ?4, 1.0) FROM diffs_fts JOIN diff_hunks d ON d.id = diffs_fts.hunk_id
                 WHERE diffs_fts MATCH ?1 AND (?3 OR d.lang = 'markdown') ORDER BY 2 LIMIT ?2",
            ),
        ] {
            let mut st = self.0.db.prepare(sql)?;
            let code = q.include_code || corpus == Corpus::Record; // records: filtered after hydration
            for row in st.query_map(rusqlite::params![m, k, code, hw], |r| Ok((r.get::<_, f64>(1)?, r.get::<_, String>(0)?)))? {
                let (score, id) = row?;
                scored.push((score, corpus, id));
            }
        }
        scored.sort_by(|a, b| a.0.total_cmp(&b.0));
        Ok(scored)
    }
}

pub struct Vector<'a> {
    pub store: &'a Store,
    pub embedder: &'a Embedder,
}

// Brute-force dot product over every stored vector, O(n·d) per query: fine to about
// 100k chunks. With the `ann` feature, models with `ann::min_vectors()` or more
// vectors go through an HNSW index that nominates candidates, re-scored exactly here.
impl Retriever for Vector<'_> {
    fn name(&self) -> String {
        format!("vector({})", self.embedder.id())
    }
    fn search(&self, q: &SearchQuery, k: usize) -> Result<Vec<Candidate>> {
        let qv = match &q.vector {
            Some(v) => v.clone(),
            None => self.embedder.embed(std::slice::from_ref(&q.text), Role::Query)?.remove(0),
        };
        let model = self.embedder.id();
        let filter = self.ann_filter(&model, &qv, k)?;
        let mut st = self.store.db.prepare(&format!(
            "SELECT 'c', CAST(c.id AS TEXT), e.vec FROM chunks c JOIN embeddings e ON e.hash = c.hash AND e.model = ?1{filter} AND (?2 OR c.lang = 'markdown')
             UNION ALL
             SELECT 'r', ri.record_id, e.vec FROM record_index ri JOIN embeddings e ON e.hash = ri.hash AND e.model = ?1{filter}
             UNION ALL
             SELECT 'd', CAST(d.id AS TEXT), e.vec FROM diff_hunks d JOIN embeddings e ON e.hash = d.hash AND e.model = ?1{filter} AND (?2 OR d.lang = 'markdown')",
        ))?;
        let mut scored: Vec<(Corpus, String, f32)> = st
            .query_map(rusqlite::params![model, q.include_code], |r| {
                // dot straight off the blob bytes: no per-row Vec allocation
                let b = r.get_ref(2)?.as_blob()?;
                let c = match r.get::<_, String>(0)?.as_str() {
                    "r" => Corpus::Record,
                    "d" => Corpus::Diff,
                    _ => Corpus::Chunk,
                };
                let dot: f32 = b.chunks_exact(4).zip(&qv).map(|(x, q)| f32::from_le_bytes([x[0], x[1], x[2], x[3]]) * q).sum();
                Ok((c, r.get(1)?, dot))
            })?
            .collect::<rusqlite::Result<_>>()?;
        scored.sort_by(|a, b| b.2.total_cmp(&a.2));
        Ok(rank_joint(scored.into_iter().map(|(c, id, _)| (c, id)), k))
    }
}

impl Vector<'_> {
    /// `AND e.id IN (...)` from the HNSW index, or "" for an exact scan.
    #[cfg(feature = "ann")]
    fn ann_filter(&self, model: &str, qv: &[f32], k: usize) -> Result<String> {
        let n: usize = self.store.db.query_row("SELECT COUNT(*) FROM embeddings WHERE model = ?1", [model], |r| r.get(0))?;
        if n < crate::ann::min_vectors() {
            return Ok(String::new());
        }
        // ponytail: 4x over-fetch covers embeddings no current item uses any more
        // (the cache keeps them); prune the index if a repo churns far more than that.
        let ids = crate::ann::nearest(&crate::ann::open(self.store, model)?, qv, k * 4)?;
        Ok(format!(" AND e.id IN ({})", ids.iter().map(u64::to_string).collect::<Vec<_>>().join(",")))
    }
    #[cfg(not(feature = "ann"))]
    fn ann_filter(&self, _: &str, _: &[f32], _: usize) -> Result<String> {
        Ok(String::new())
    }
}

/// Ranks across corpora, so a weak match in a tiny corpus (a handful of curated
/// records) can't score like a strong match in a big one.
pub fn rank_joint(best_first: impl IntoIterator<Item = (Corpus, String)>, k: usize) -> Vec<Candidate> {
    best_first.into_iter().take(k).enumerate().map(|(rank, (corpus, id))| Candidate { corpus, id, rank }).collect()
}

/// Reciprocal Rank Fusion (k = 60).
pub fn rrf(lists: &[(String, Vec<Candidate>)]) -> Vec<(Corpus, String, f64, Vec<String>)> {
    let mut acc: HashMap<(Corpus, String), (f64, Vec<String>)> = HashMap::new();
    for (name, list) in lists {
        for c in list {
            let e = acc.entry((c.corpus, c.id.clone())).or_default();
            e.0 += 1.0 / (60.0 + c.rank as f64 + 1.0);
            e.1.push(name.clone());
        }
    }
    let mut out: Vec<_> = acc.into_iter().map(|((c, id), (s, by))| (c, id, s, by)).collect();
    out.sort_by(|a, b| b.2.total_cmp(&a.2).then(a.0.cmp(&b.0)).then(a.1.cmp(&b.1)));
    out
}

/// How well the retrievers agree on a hit, in words an agent can use (a fused
/// score of 0.03 says nothing): every retriever ranked it in its top 10 →
/// "strong"; at least one did → "partial"; else "weak".
fn match_label(lists: &[(String, Vec<Candidate>)], corpus: Corpus, id: &str) -> &'static str {
    let ranks: Vec<usize> = lists.iter().filter_map(|(_, l)| l.iter().find(|c| c.corpus == corpus && c.id == id).map(|c| c.rank)).collect();
    let top = ranks.iter().filter(|&&r| r < 10).count();
    if top == lists.len() && (lists.len() > 1 || ranks.first().is_some_and(|&r| r < 3)) {
        "strong"
    } else if top > 0 {
        "partial"
    } else {
        "weak"
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct CodeRef {
    pub name: String,
    pub path: String,
    pub line: usize,
}

#[derive(Clone, Debug, Serialize)]
pub struct Hit {
    pub corpus: Corpus,
    pub id: String,
    pub score: f64,
    /// decision, spec, doc, code, history, agent or marketing (kinds.rs)
    pub kind: String,
    /// strong / partial / weak retriever agreement (see `match_label`)
    #[serde(rename = "match")]
    pub matched: String,
    /// Code that defines names this passage mentions in backticks (docs only).
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub code_refs: Vec<CodeRef>,
    pub retrievers: Vec<String>,
    pub validity: String,
    pub path: String,
    pub revision: String,
    pub lines: (usize, usize),
    pub heading_path: Vec<String>,
    pub scope: String,
    pub text: String,
    /// For records: the sentence(s) the highlight sits in, with the highlight in **bold**.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context: Option<String>,
    /// Chunks: position in the current source as UTF-16 offsets (JS string
    /// indices), so a UI can mark the passage.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub offsets: Option<(usize, usize)>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub record: Option<Record>,
}

#[derive(Debug, Serialize)]
pub struct SearchResult {
    pub hits: Vec<Hit>,
    pub retrievers: Vec<String>,
    pub warnings: Vec<String>,
}

pub type Retrievers<'a> = Vec<Box<dyn Retriever + 'a>>;

/// SQLite baseline: FTS5 + brute-force vectors when an embedder is configured.
pub fn default_retrievers<'a>(store: &'a Store, embedder: Option<&'a Embedder>) -> Retrievers<'a> {
    let mut r: Retrievers<'a> = vec![Box::new(Lexical(store))];
    if let Some(e) = embedder {
        r.push(Box::new(Vector { store, embedder: e }));
    }
    r
}

pub fn search(store: &Store, embedder: Option<&Embedder>, q: &SearchQuery) -> Result<SearchResult> {
    search_with(store, &default_retrievers(store, embedder), q)
}

pub fn search_with(store: &Store, retrievers: &[Box<dyn Retriever + '_>], q: &SearchQuery) -> Result<SearchResult> {
    let k = q.limit.max(10) * 4;
    let (mut lists, mut warnings) = (Vec::new(), Vec::new());
    for r in retrievers {
        match r.search(q, k) {
            Ok(c) => lists.push((r.name(), c)),
            Err(e) => warnings.push(format!("{} unavailable: {e:#}", r.name())),
        }
    }
    let reranker = RERANKER.get();
    let priors = std::env::var("ORBIT_KIND_PRIORS").as_deref() != Ok("0");
    // the kind prior and the reranker re-order, so both look past the requested limit
    let depth = q.limit.max(if reranker.is_some() { RERANK_DEPTH } else { 0 }).max(if priors { q.limit * PRIOR_DEPTH } else { 0 });
    let mut snaps: HashMap<String, String> = HashMap::new();
    let mut seen = HashSet::new();
    let mut hits = Vec::new();
    for (corpus, id, score, by) in rrf(&lists) {
        let Some(mut h) = hydrate(store, &mut snaps, corpus, &id, score, by)? else { continue };
        h.matched = match_label(&lists, corpus, &id).into();
        h.score *= store.kinds.weight(&h.kind);
        if priors && h.corpus == Corpus::Chunk && h.kind != "code" && prose_words(&h.text) < THIN_WORDS {
            h.score *= THIN_WEIGHT; // a "Related: [[..]]" footer matches on link titles, not content
        }
        let in_scope = q.scope.as_deref().is_none_or(|s| h.scope.starts_with(s) || h.path.starts_with(s));
        let active = q.include_inactive || !matches!(h.validity.as_str(), "retired" | "superseded");
        // also catches retrievers that can't filter by language (LanceDB)
        let lang_ok = q.include_code || crate::code::language(&h.path).is_none();
        if in_scope && active && lang_ok && seen.insert(crate::sources::sha256(&h.text)) {
            hits.push(h);
        }
        if hits.len() >= depth {
            break;
        }
    }
    let mut names: Vec<String> = lists.into_iter().map(|l| l.0).collect();
    if let Some(r) = reranker {
        let docs: Vec<String> = hits.iter().map(|h| h.context.clone().unwrap_or_else(|| h.text.chars().take(2000).collect())).collect();
        match r.scores(&q.text, &docs.iter().map(String::as_str).collect::<Vec<_>>()) {
            Ok(sc) => {
                let mut order: Vec<usize> = (0..hits.len()).collect();
                order.sort_by(|&a, &b| sc[b].total_cmp(&sc[a]));
                let mut slots: Vec<Option<Hit>> = hits.into_iter().map(Some).collect();
                // re-derive an RRF-scale score from the new rank, so bundle boosts keep their meaning
                hits = order.iter().enumerate().map(|(rank, &i)| Hit { score: 2.0 / (61.0 + rank as f64), ..slots[i].take().unwrap() }).collect();
                names.push(r.name());
            }
            Err(e) => warnings.push(format!("{} unavailable: {e:#}", r.name())),
        }
    }
    if priors && reranker.is_none() {
        hits.sort_by(|a, b| b.score.total_cmp(&a.score)); // stable: equal scores keep fused order
    }
    hits.truncate(q.limit);
    crate::coderefs::attach(store, &mut hits)?;
    Ok(SearchResult { hits, retrievers: names, warnings })
}

pub fn record_hit(store: &Store, snaps: &mut HashMap<String, String>, r: Record, score: f64, by: Vec<String>) -> Result<Hit> {
    let text = snaps.entry(r.snapshot_hash.clone()).or_insert(store.snapshot(&r.snapshot_hash)?).clone();
    let lines = r.start.zip(r.end).map_or((0, 0), |(s, e)| (line_of(&text, s), line_of(&text, e)));
    let context = r.start.zip(r.end).filter(|&(_, e)| e <= text.len()).map(|(s, e)| sentence_around(&text, s, e));
    Ok(Hit {
        context,
        corpus: Corpus::Record,
        id: r.id.clone(),
        score,
        retrievers: by,
        validity: r.validity().into(),
        path: r.path.clone(),
        revision: r.revision_label(),
        lines,
        heading_path: r.heading_path.clone(),
        scope: r.scope.clone(),
        text: r.body.clone(),
        offsets: None,
        kind: store.kinds.classify(&r.path, &text).into(),
        matched: String::new(),
        code_refs: vec![],
        record: Some(r),
    })
}

/// Expand a selection to its enclosing sentence(s) within the block (capped),
/// marking the selection in bold: a highlight is a fragment, an agent needs the claim.
pub fn sentence_around(text: &str, s: usize, e: usize) -> String {
    use crate::anchors::{ceil_char, floor_char};
    let lo = floor_char(text, s.saturating_sub(300));
    let hi = ceil_char(text, e + 300);
    let before = &text[lo..s];
    let start = [". ", "\n\n", "\n- ", "\n* ", "\n"].iter().filter_map(|m| before.rfind(m).map(|i| lo + i + m.len())).max().unwrap_or(lo);
    let after = &text[e..hi];
    let end = [". ", "\n"].iter().filter_map(|m| after.find(m).map(|i| e + i + if *m == ". " { 1 } else { 0 })).min().unwrap_or(hi);
    let one = |t: &str| t.split_whitespace().collect::<Vec<_>>().join(" ");
    format!("{}**{}**{}", one(&text[start..s]) + if text[start..s].ends_with(char::is_whitespace) { " " } else { "" }, one(&text[s..e]),
        if text[e..end].starts_with(char::is_whitespace) { " " } else { "" }.to_string() + &one(&text[e..end]))
}

fn hydrate(store: &Store, snaps: &mut HashMap<String, String>, corpus: Corpus, id: &str, score: f64, by: Vec<String>) -> Result<Option<Hit>> {
    use rusqlite::OptionalExtension;
    if corpus == Corpus::Record {
        return Ok(Some(record_hit(store, snaps, store.record(id)?, score, by)?));
    }
    if corpus == Corpus::Diff {
        let row = store
            .db
            .query_row("SELECT base, head, path, new_start, new_lines, text FROM diff_hunks WHERE id = ?1", [id], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, String>(2)?, r.get::<_, usize>(3)?, r.get::<_, usize>(4)?, r.get::<_, String>(5)?))
            })
            .optional()?;
        let Some((base, head, path, ns, nl, text)) = row else { return Ok(None) };
        return Ok(Some(Hit {
            corpus,
            id: format!("diff_{id}"),
            score,
            retrievers: by,
            validity: "historical".into(), // a change, not a statement of current truth
            scope: path.clone(),
            path,
            revision: format!("git:{base}..{head}"),
            lines: (ns, (ns + nl).saturating_sub(1).max(ns)),
            heading_path: vec!["diff".into(), format!("{base}..{head}")],
            text,
            context: None,
            offsets: None,
            kind: "history".into(), // a change, like the validity says
            matched: String::new(),
            code_refs: vec![],
            record: None,
        }));
    }
    let row = store
        .db
        .query_row(
            "SELECT c.heading_path, c.scope, c.start_offset, c.end_offset, c.text, s.path, r.git_rev, r.snapshot_hash, c.kind
             FROM chunks c JOIN source_revisions r ON r.id = c.revision_id JOIN sources s ON s.id = r.source_id WHERE c.id = ?1",
            [id],
            |r| {
                Ok((
                    (r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, usize>(2)?, r.get::<_, usize>(3)?, r.get::<_, String>(4)?),
                    (r.get::<_, String>(5)?, r.get::<_, Option<String>>(6)?, r.get::<_, String>(7)?, r.get::<_, String>(8)?),
                ))
            },
        )
        .optional()?;
    let Some(((hp, scope, s, e, text), (path, git, snap, kind))) = row else { return Ok(None) };
    let content = snaps.entry(snap.clone()).or_insert(store.snapshot(&snap)?);
    Ok(Some(Hit {
        corpus,
        id: format!("chunk_{id}"),
        score,
        retrievers: by,
        validity: "current".into(), // chunks exist only for the latest revision
        lines: (line_of(content, s), line_of(content, e)),
        path,
        revision: git.map(|g| format!("git:{g}")).unwrap_or_else(|| format!("snap:{}", &snap[..12])),
        heading_path: serde_json::from_str(&hp).unwrap_or_default(),
        scope,
        text,
        context: None,
        offsets: Some((crate::storage::byte_to_utf16(content, s), crate::storage::byte_to_utf16(content, e))),
        kind,
        matched: String::new(),
        code_refs: vec![],
        record: None,
    }))
}

#[cfg(test)]
mod tests {
    #[test]
    fn link_footers_are_thin() {
        assert!(super::prose_words("Related: [[Tech Decisions]], [[2026-09-23 The Tax Worker Is A Rust Process|TECH-019]].") < super::THIN_WORDS);
        assert!(super::prose_words("The move's sub-run matches with exactly one lot rule, the user's method.") >= super::THIN_WORDS);
    }

    #[test]
    fn sentence_around_marks_the_highlight_in_its_sentence() {
        let t = "## Images\n\nForecast imagery and spot photos are served as AVIF with a JPEG fallback for clients. Thumbnails are small.\n";
        let s = t.find("served as AVIF").unwrap();
        let e = s + "served as AVIF with a JPEG fallback".len();
        assert_eq!(super::sentence_around(t, s, e), "Forecast imagery and spot photos are **served as AVIF with a JPEG fallback** for clients.");
    }
}
