use crate::anchors::{self, Quote};
use crate::storage::Store;
use anyhow::{bail, Result};
use pulldown_cmark::{Event, Options, Parser, Tag};
use rusqlite::{params, OptionalExtension};
use serde::Serialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::path::Path;
use std::process::Command;

const CHUNK_BYTES: usize = 1600; // ~400 tokens

pub fn sha256(s: &str) -> String {
    Sha256::digest(s.as_bytes()).iter().map(|b| format!("{b:02x}")).collect()
}

pub fn slug(path: &[String]) -> String {
    path.iter()
        .map(|h| {
            let s: String = h.to_lowercase().chars().map(|c| if c.is_alphanumeric() { c } else { '-' }).collect();
            s.split('-').filter(|p| !p.is_empty()).collect::<Vec<_>>().join("-")
        })
        .collect::<Vec<_>>()
        .join("/")
}

#[derive(Debug, Clone)]
pub struct Block {
    pub heading_path: Vec<String>,
    pub start: usize,
    pub end: usize,
    pub heading: bool,
}

/// Top-level Markdown blocks with their heading path (AST, not regex).
pub fn blocks(text: &str) -> Vec<Block> {
    let opts = Options::ENABLE_TABLES | Options::ENABLE_STRIKETHROUGH | Options::ENABLE_TASKLISTS;
    let (mut out, mut stack, mut depth) = (Vec::new(), Vec::<(usize, String)>::new(), 0usize);
    for (ev, range) in Parser::new_ext(text, opts).into_offset_iter() {
        match ev {
            Event::Start(tag) => {
                if depth == 0 {
                    let heading = if let Tag::Heading { level, .. } = tag {
                        let title = text[range.clone()].lines().next().unwrap_or("");
                        let title = title.trim().trim_start_matches('#').trim_end_matches('#').trim().to_string();
                        stack.retain(|(l, _)| *l < level as usize);
                        stack.push((level as usize, title));
                        true
                    } else {
                        false
                    };
                    let heading_path = stack.iter().map(|(_, t)| t.clone()).collect();
                    out.push(Block { heading_path, start: range.start, end: range.end, heading });
                }
                depth += 1;
            }
            Event::End(_) => depth -= 1,
            _ => {}
        }
    }
    out
}

pub fn is_markdown(path: &str) -> bool {
    path.ends_with(".md") || path.ends_with(".markdown")
}

pub fn supported(path: &str) -> bool {
    is_markdown(path) || crate::code::language(path).is_some()
}

pub fn fence_lang(path: &str) -> &'static str {
    crate::code::language(path).map_or("markdown", |l| l.1)
}

/// Structural blocks for any supported source: Markdown AST or tree-sitter symbols.
pub fn structure(path: &str, text: &str) -> Vec<Block> {
    match crate::code::language(path) {
        Some((lang, _)) => crate::code::blocks(text, &lang),
        None => blocks(text),
    }
}

pub fn heading_path_at(path: &str, text: &str, offset: usize) -> Vec<String> {
    structure(path, text).into_iter().take_while(|b| b.start <= offset).last().map(|b| b.heading_path).unwrap_or_default()
}

/// Section chunks: consecutive blocks under one heading/symbol, merged up to
/// CHUNK_BYTES; a header (heading, `impl X {` line) folds into a following
/// child; oversized blocks split on whitespace. Offsets are preserved.
pub fn chunks(path: &str, text: &str) -> Vec<Block> {
    let mut out: Vec<Block> = Vec::new();
    for b in structure(path, text) {
        match out.last_mut() {
            Some(last) if last.heading_path == b.heading_path && b.end - last.start <= CHUNK_BYTES => {
                last.end = b.end;
                last.heading = false;
            }
            Some(last) if last.heading && b.heading_path.starts_with(&last.heading_path) && b.end - last.start <= CHUNK_BYTES => {
                last.end = b.end;
                last.heading = false;
                last.heading_path = b.heading_path;
            }
            _ => out.push(b),
        }
    }
    if is_markdown(path) {
        out.retain(|c| !c.heading); // a bare heading is not evidence
    }
    let mut split = Vec::new();
    for b in out {
        let mut s = b.start;
        while b.end - s > CHUNK_BYTES * 3 / 2 {
            let cut = anchors::floor_char(text, s + CHUNK_BYTES);
            let cut = text[s..cut].rfind(char::is_whitespace).map_or(cut, |i| s + i);
            split.push(Block { start: s, end: cut, ..b.clone() });
            s = cut;
        }
        split.push(Block { start: s, ..b });
    }
    split
}

fn git_rev(root: &Path, rel: &str) -> Option<String> {
    let run = |args: &[&str]| {
        let o = Command::new("git").arg("-C").arg(root).args(args).output().ok()?;
        o.status.success().then(|| String::from_utf8_lossy(&o.stdout).trim().to_string())
    };
    let head = run(&["rev-parse", "--short=12", "HEAD"])?;
    let dirty = !run(&["status", "--porcelain", "--", rel])?.is_empty();
    Some(if dirty { format!("{head}+dirty") } else { head })
}

#[derive(Debug, Serialize, Clone)]
pub struct Revision {
    pub id: i64,
    pub source_id: String,
    pub path: String,
    pub snapshot_hash: String,
    pub git_rev: Option<String>,
}

#[derive(Debug, Default, Serialize)]
pub struct SyncReport {
    pub revised: Vec<String>,
    pub reanchored: Vec<(String, String)>, // (record, resolution)
    pub flagged: Vec<String>,
}

/// Text that gets embedded/hashed for a chunk or a record.
pub fn index_text(heading: &[String], body: &str) -> String {
    format!("{}\n{}", heading.join(" > "), body.trim())
}

impl Store {
    pub fn source_by_path(&self, rel: &str) -> Result<Option<String>> {
        Ok(self.db.query_row("SELECT id FROM sources WHERE path = ?1", [rel], |r| r.get(0)).optional()?)
    }

    /// Register (if new) and sync a Markdown file. Returns the source id.
    pub fn open_source(&self, path: &Path) -> Result<String> {
        let rel = self.rel_path(path)?;
        let id = match self.source_by_path(&rel)? {
            Some(id) => id,
            None => {
                let id = self.next_id("sources", "src")?;
                self.db.execute("INSERT INTO sources(id, path) VALUES (?1, ?2)", params![id, rel])?;
                self.event("source.registered", &id, json!({ "path": rel }))?;
                id
            }
        };
        self.sync_source(&id)?;
        Ok(id)
    }

    pub fn latest_revision(&self, source_id: &str) -> Result<Option<Revision>> {
        Ok(self
            .db
            .query_row(
                "SELECT id, source_id, path, snapshot_hash, git_rev FROM source_revisions WHERE source_id = ?1 ORDER BY id DESC LIMIT 1",
                [source_id],
                |r| Ok(Revision { id: r.get(0)?, source_id: r.get(1)?, path: r.get(2)?, snapshot_hash: r.get(3)?, git_rev: r.get(4)? }),
            )
            .optional()?)
    }

    pub fn sync_all(&self) -> Result<SyncReport> {
        let ids: Vec<String> = self.db.prepare("SELECT id FROM sources")?.query_map([], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
        let mut total = SyncReport::default();
        for id in ids {
            let r = self.sync_source(&id)?;
            total.revised.extend(r.revised);
            total.reanchored.extend(r.reanchored);
            total.flagged.extend(r.flagged);
        }
        Ok(total)
    }

    pub fn sync_source(&self, id: &str) -> Result<SyncReport> {
        let mut report = SyncReport::default();
        let mut path: String = self.db.query_row("SELECT path FROM sources WHERE id = ?1", [id], |r| r.get(0))?;
        let latest = self.latest_revision(id)?;
        if !self.root.join(&path).is_file() {
            let renamed = match &latest {
                Some(l) => self.find_renamed(&l.path, &self.snapshot(&l.snapshot_hash)?)?,
                None => None,
            };
            match renamed {
                Some(new_path) => {
                    self.db.execute("UPDATE sources SET path = ?1 WHERE id = ?2", params![new_path, id])?;
                    self.event("source.renamed", id, json!({ "from": path, "to": new_path }))?;
                    path = new_path;
                }
                None => {
                    self.flag_source_missing(id, &mut report)?;
                    return Ok(report);
                }
            }
        }
        let content = std::fs::read_to_string(self.root.join(&path))?;
        let hash = sha256(&content);
        if latest.as_ref().is_some_and(|l| l.snapshot_hash == hash && l.path == path) {
            return Ok(report);
        }

        let tx = self.db.unchecked_transaction()?;
        tx.execute("INSERT OR IGNORE INTO snapshots(hash, content) VALUES (?1, ?2)", params![hash, content])?;
        tx.execute(
            "INSERT INTO source_revisions(source_id, snapshot_hash, path, git_rev) VALUES (?1, ?2, ?3, ?4)",
            params![id, hash, path, git_rev(&self.root, &path)],
        )?;
        let rev_id = tx.last_insert_rowid();
        self.reindex_chunks(id, rev_id, &path, &content)?;

        if latest.is_some() {
            for rec in self.records_where("source_id = ?1 AND status NOT IN ('retired','superseded')", [id])? {
                let Some((start, end)) = rec.start.zip(rec.end) else { continue };
                let old = self.snapshot(&rec.snapshot_hash)?;
                let q = Quote { exact: rec.exact.clone(), prefix: rec.prefix.clone(), suffix: rec.suffix.clone(), start, end };
                let r = anchors::resolve(&old, &content, &q);
                let nq = r.range.map(|(s, e)| anchors::quote(&content, s, e)).unwrap_or(q);
                let heading = r.range.map(|(s, _)| heading_path_at(&path, &content, s)).unwrap_or(rec.heading_path.clone());
                tx.execute(
                    "INSERT INTO anchors(record_id, source_revision_id, exact, prefix, suffix, start_offset, end_offset, heading_path, resolution)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
                    params![rec.id, rev_id, nq.exact, nq.prefix, nq.suffix, r.range.map(|x| x.0), r.range.map(|x| x.1),
                            serde_json::to_string(&heading)?, r.resolution.as_str()],
                )?;
                self.index_record(&rec.id)?;
                report.reanchored.push((rec.id.clone(), r.resolution.as_str().into()));
                if !r.resolution.confident() {
                    self.open_review(&rec.id, r.resolution.as_str())?;
                    report.flagged.push(rec.id.clone());
                }
            }
        }
        self.event("source.revised", id, json!({ "revision": rev_id, "hash": hash, "reanchored": report.reanchored }))?;
        tx.commit()?;
        report.revised.push(id.to_string());
        Ok(report)
    }

    /// A missing source's new path: an unregistered file with identical content, else
    /// the one clearly most similar by line-set Jaccard (moved *and* edited). The
    /// edits are then handled by normal re-anchoring.
    // ponytail: reads every candidate file of the same type; fine for repos, index by hash if huge
    fn find_renamed(&self, old_path: &str, old: &str) -> Result<Option<String>> {
        let ext = |p: &str| p.rsplit('.').next().unwrap_or("").to_string();
        let lines = |t: &str| t.lines().map(str::trim).filter(|l| !l.is_empty()).map(String::from).collect::<std::collections::HashSet<_>>();
        let (hash, old_lines) = (sha256(old), lines(old));
        let mut scored = Vec::new();
        for f in self.source_files()? {
            if ext(&f) != ext(old_path) || self.source_by_path(&f)?.is_some() {
                continue;
            }
            let Ok(c) = std::fs::read_to_string(self.root.join(&f)) else { continue };
            if sha256(&c) == hash {
                return Ok(Some(f));
            }
            let nl = lines(&c);
            let j = old_lines.intersection(&nl).count() as f64 / old_lines.union(&nl).count().max(1) as f64;
            scored.push((j, f));
        }
        scored.sort_by(|a, b| b.0.total_cmp(&a.0));
        Ok(match scored.as_slice() {
            [(best, f), rest @ ..] if *best >= 0.6 && rest.first().is_none_or(|r| best - r.0 >= 0.1) => Some(f.clone()),
            _ => None,
        })
    }

    fn flag_source_missing(&self, id: &str, report: &mut SyncReport) -> Result<()> {
        for rec in self.records_where("source_id = ?1 AND status = 'active'", [id])? {
            self.open_review(&rec.id, "source_missing")?;
            report.flagged.push(rec.id);
        }
        Ok(())
    }

    /// Mark a record needs_review and open a review item (at most one open per record).
    pub fn open_review(&self, record_id: &str, reason: &str) -> Result<()> {
        let n = self.db.execute("INSERT OR IGNORE INTO review_items(record_id, reason, state) VALUES (?1, ?2, 'open')", params![record_id, reason])?;
        if self.record(record_id)?.status == "active" {
            self.revise(record_id, |r| r.status = "needs_review".into())?;
        }
        if n > 0 {
            self.event("review.opened", record_id, json!({ "reason": reason }))?;
        }
        Ok(())
    }

    pub(crate) fn reindex_chunks(&self, source_id: &str, rev_id: i64, path: &str, content: &str) -> Result<()> {
        self.db.execute(
            "DELETE FROM chunks_fts WHERE chunk_id IN (SELECT c.id FROM chunks c JOIN source_revisions r ON r.id = c.revision_id WHERE r.source_id = ?1)",
            [source_id],
        )?;
        self.db.execute("DELETE FROM chunks WHERE revision_id IN (SELECT id FROM source_revisions WHERE source_id = ?1)", [source_id])?;
        let lang = fence_lang(path);
        let mut tags = crate::tags::tags(path, content).into_iter().peekable();
        for c in chunks(path, content) {
            let text = &content[c.start..c.end];
            let it = index_text(&c.heading_path, text);
            let h = sha256(&it);
            self.db.execute(
                "INSERT INTO chunks(revision_id, heading_path, scope, start_offset, end_offset, text, hash, lang) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                params![rev_id, serde_json::to_string(&c.heading_path)?, slug(&c.heading_path), c.start, c.end, text, h, lang],
            )?;
            let cid = self.db.last_insert_rowid();
            // tags are sorted by start; one belongs to the chunk its start falls in
            while let Some(t) = tags.next_if(|t| t.start < c.end) {
                if t.start >= c.start {
                    self.db.execute(
                        "INSERT INTO tags(chunk_id, kind, name, value, start_offset, end_offset) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                        params![cid, t.kind, t.name, t.value, t.start, t.end],
                    )?;
                }
            }
            self.db.execute("INSERT INTO chunks_fts(chunk_id, heading, text) VALUES (?1, ?2, ?3)", params![cid, c.heading_path.join(" "), text])?;
            self.db.execute("INSERT OR IGNORE INTO embed_outbox(hash, text) VALUES (?1, ?2)", params![h, it])?;
        }
        Ok(())
    }

    pub fn index_record(&self, id: &str) -> Result<()> {
        let r = self.record(id)?;
        // index the highlight in its sentence: a fragment ("PostHog, EU cloud") does
        // not match the question it answers ("which analytics vendor?")
        let snap = self.snapshot(&r.snapshot_hash)?;
        let claim = r.start.zip(r.end).filter(|&(_, e)| e <= snap.len()).map_or(r.body.clone(), |(s, e)| crate::retrieval::sentence_around(&snap, s, e).replace("**", ""));
        let text = format!("{}: {}\n{}", r.category, claim, r.note.clone().unwrap_or_default());
        let it = index_text(&r.heading_path, &text);
        let h = sha256(&it);
        self.db.execute("DELETE FROM records_fts WHERE record_id = ?1", [id])?;
        self.db.execute(
            "INSERT INTO records_fts(record_id, heading, body, note) VALUES (?1, ?2, ?3, ?4)",
            params![id, r.heading_path.join(" "), format!("{} {}", r.category, claim), r.note],
        )?;
        self.db.execute("INSERT OR REPLACE INTO record_index(record_id, hash) VALUES (?1, ?2)", params![id, h])?;
        self.db.execute("INSERT OR IGNORE INTO embed_outbox(hash, text) VALUES (?1, ?2)", params![h, it])?;
        Ok(())
    }

    /// Capture a highlight from the latest revision of a source (byte offsets).
    #[allow(clippy::too_many_arguments)]
    pub fn capture(&self, source_id: &str, revision_id: i64, start: usize, end: usize, category: &str, note: Option<&str>, scope: Option<&str>, proposed: bool) -> Result<String> {
        let latest = self.latest_revision(source_id)?.ok_or_else(|| anyhow::anyhow!("unknown source {source_id}"))?;
        if latest.id != revision_id {
            bail!("stale revision {revision_id} (latest is {}); reload the document", latest.id);
        }
        let text = self.snapshot(&latest.snapshot_hash)?;
        if start >= end || end > text.len() || !text.is_char_boundary(start) || !text.is_char_boundary(end) || text[start..end].trim().is_empty() {
            bail!("invalid selection {start}..{end}");
        }
        let q = anchors::quote(&text, start, end);
        let heading = heading_path_at(&latest.path, &text, start);
        let scope = scope.map(str::to_string).unwrap_or_else(|| slug(&heading));
        let authority = if proposed { "proposed" } else { "human_approved" };

        let tx = self.db.unchecked_transaction()?;
        let id = self.next_id("records", "ctx")?;
        tx.execute("INSERT INTO records(id) VALUES (?1)", [&id])?;
        tx.execute(
            "INSERT INTO record_revisions(record_id, rev, category, body, note, scope, status, authority) VALUES (?1, 1, ?2, ?3, ?4, ?5, 'active', ?6)",
            params![id, category, q.exact, note, scope, authority],
        )?;
        tx.execute(
            "INSERT INTO anchors(record_id, source_revision_id, exact, prefix, suffix, start_offset, end_offset, heading_path, resolution)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 'captured')",
            params![id, revision_id, q.exact, q.prefix, q.suffix, start, end, serde_json::to_string(&heading)?],
        )?;
        self.index_record(&id)?;
        self.event("record.captured", &id, json!({ "source": source_id, "revision": revision_id, "start": start, "end": end, "category": category }))?;
        tx.commit()?;
        Ok(id)
    }

    pub fn markdown_files(&self) -> Result<Vec<String>> {
        Ok(self.source_files()?.into_iter().filter(|f| is_markdown(f)).collect())
    }

    /// Supported source files under the root (skips dot-dirs, node_modules, target, symlinks).
    pub fn source_files(&self) -> Result<Vec<String>> {
        fn walk(dir: &Path, root: &Path, out: &mut Vec<String>) {
            let Ok(rd) = std::fs::read_dir(dir) else { return };
            for e in rd.flatten() {
                let name = e.file_name().to_string_lossy().to_string();
                let Ok(ft) = e.file_type() else { continue };
                if name.starts_with('.') || name == "node_modules" || name == "target" || ft.is_symlink() {
                    continue;
                }
                let p = e.path();
                if ft.is_dir() {
                    walk(&p, root, out);
                } else if supported(&name) {
                    out.push(p.strip_prefix(root).unwrap().to_string_lossy().replace('\\', "/"));
                }
            }
        }
        let mut out = Vec::new();
        walk(&self.root, &self.root, &mut out);
        out.sort();
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chunks_follow_headings_and_keep_offsets() {
        let t = "# A\n\npara one\n\n- x\n- y\n\n## B\n\n| a | b |\n|---|---|\n| 1 | 2 |\n";
        let c = chunks("a.md", t);
        assert_eq!(c.iter().map(|b| b.heading_path.join(">")).collect::<Vec<_>>(), ["A", "A>B"]);
        assert!(t[c[0].start..c[0].end].starts_with("# A") && t[c[0].start..c[0].end].trim_end().ends_with("- y"));
        assert_eq!(heading_path_at("a.md", t, t.find("| 1").unwrap()), ["A", "B"]);
        assert_eq!(slug(&["Architecture".into(), "Native Navigation!".into()]), "architecture/native-navigation");
    }
}
