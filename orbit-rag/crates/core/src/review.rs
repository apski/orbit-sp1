use crate::storage::{Record, Store};
use anyhow::{bail, Result};
use rusqlite::params;
use serde::Serialize;
use serde_json::json;
use std::collections::HashSet;

pub const EDGE_KINDS: &[&str] = &["supports", "contradicts", "depends_on", "supersedes", "implements", "relates_to"];

#[derive(Debug, Serialize)]
pub struct ReviewItem {
    pub id: i64,
    pub reason: String,
    pub opened: String,
    pub record: Record,
    /// The selection as last approved (captured or accepted), for comparison.
    pub approved: Option<(String, String)>, // (exact, revision label)
}

#[derive(Debug, Serialize)]
pub struct Edge {
    pub src: String,
    pub kind: String,
    pub dst: String,
    pub provenance: String,
}

#[derive(Debug, Serialize)]
pub struct Impact {
    pub record: Record,
    pub depth: usize,
    pub via: Option<String>, // "ctx_002 depends_on ctx_001"
}

/// The passage of a decision doc that states the decision: a `**Decision:**` line or
/// bullet, else the first paragraph under a `## Decision` heading, else the first
/// paragraph after the title that isn't metadata (status, date, frontmatter).
/// Byte range, trimmed; `None` when the doc has no prose.
pub fn decision_passage(text: &str) -> Option<(usize, usize)> {
    let mut paras: Vec<(usize, usize)> = Vec::new(); // blank-line or bullet separated blocks
    let mut start: Option<usize> = None;
    let mut at = 0;
    let mut in_front = text.starts_with("---");
    for (i, line) in text.split_inclusive('\n').enumerate() {
        let t = line.trim();
        if in_front {
            if i > 0 && t == "---" {
                in_front = false;
            }
            at += line.len();
            continue;
        }
        let bullet = t.starts_with("- ") || t.starts_with("* ");
        if t.is_empty() || t.starts_with('#') || bullet {
            if let Some(s) = start.take() {
                paras.push((s, at));
            }
        }
        if t.starts_with('#') {
            paras.push((at, at + line.len())); // headings are their own block
        } else if !t.is_empty() && start.is_none() {
            start = Some(at);
        }
        at += line.len();
    }
    if let Some(s) = start {
        paras.push((s, at));
    }
    let body = |(s, e): (usize, usize)| text[s..e].trim();
    let trimmed = |(s, e): (usize, usize)| {
        let raw = &text[s..e];
        let lead = raw.len() - raw.trim_start().len();
        (s + lead, s + lead + raw.trim().len())
    };
    let plain = |b: &str| b.replace("**", "").trim_start_matches(['-', '*', ' ']).to_lowercase();
    let indent = |(s, _): (usize, usize)| text[s..].len() - text[s..].trim_start_matches(' ').len();
    if let Some(k) = paras.iter().position(|p| plain(body(*p)).starts_with("decision:")) {
        // a `- **Decision:**` bullet owns the sub-bullets nested under it
        let end = paras[k + 1..].iter().take_while(|p| indent(**p) > indent(paras[k])).last().map_or(paras[k].1, |p| p.1);
        return Some(trimmed((paras[k].0, end)));
    }
    if let Some(i) = paras.iter().position(|p| {
        let b = body(*p).to_lowercase();
        b.starts_with('#') && b.trim_start_matches('#').trim().starts_with("decision")
    }) {
        if let Some(p) = paras[i + 1..].iter().find(|p| !body(**p).starts_with('#')) {
            return Some(trimmed(*p));
        }
    }
    let meta = |b: &str| ["status:", "date:", "owner:", "deciders:", "author:", "created:"].iter().any(|m| plain(b).contains(m) && b.len() < 400);
    paras.iter().find(|p| !body(**p).starts_with('#') && !meta(body(**p))).map(|p| trimmed(*p))
}

/// `<what we will do>`-style placeholders outside code spans.
fn is_template(t: &str) -> bool {
    t.split('`').step_by(2).any(|plain| {
        plain.match_indices('<').any(|(i, _)| plain[i + 1..].starts_with(|c: char| c.is_ascii_lowercase()) && plain[i..].find('>').is_some_and(|j| j < 80 && !plain[i..i + j].contains('\n')))
    })
}

impl Store {
    /// Offer every decision doc's decision passage as a proposed record with a review
    /// item, so a person can approve it in one step. Nothing here is authoritative until
    /// accepted. Skips docs that already have a record. Returns (path, record id).
    pub fn propose_decisions(&self, dry_run: bool) -> Result<Vec<(String, String)>> {
        let docs: Vec<(String, i64, String, String)> = self
            .db
            .prepare(
                "SELECT DISTINCT s.id, r.id, s.path, r.snapshot_hash FROM chunks c JOIN source_revisions r ON r.id = c.revision_id
                 JOIN sources s ON s.id = r.source_id WHERE c.kind = 'decision' ORDER BY s.path",
            )?
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?
            .collect::<rusqlite::Result<_>>()?;
        let mut out = Vec::new();
        for (src, rev, path, snap) in docs {
            if !self.records_where("source_id = ?1 AND status <> 'retired'", [&src])?.is_empty() {
                continue;
            }
            let text = self.snapshot(&snap)?;
            let Some((s, e)) = decision_passage(&text) else { continue };
            if is_template(&text[s..e]) {
                continue; // an index or template note, e.g. "**Decision:** <what we will do>"
            }
            if dry_run {
                out.push((path, text[s..e].chars().take(80).collect()));
                continue;
            }
            let id = self.capture(&src, rev, s, e, "decision", Some("proposed from a decision doc: accept to make it authoritative"), None, true)?;
            self.db.execute("INSERT OR IGNORE INTO review_items(record_id, reason, state) VALUES (?1, 'proposed', 'open')", [&id])?;
            self.event("record.proposed", &id, json!({ "path": path }))?;
            out.push((path, id));
        }
        Ok(out)
    }

    pub fn review_list(&self) -> Result<Vec<ReviewItem>> {
        let rows: Vec<(i64, String, String, String)> = self
            .db
            .prepare("SELECT id, record_id, reason, created_at FROM review_items WHERE state = 'open' ORDER BY id")?
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?
            .collect::<rusqlite::Result<_>>()?;
        rows.into_iter()
            .map(|(id, rec, reason, opened)| {
                let approved = self
                    .db
                    .query_row(
                        "SELECT a.exact, COALESCE('git:' || sr.git_rev, 'rev ' || sr.id) FROM anchors a JOIN source_revisions sr ON sr.id = a.source_revision_id
                         WHERE a.record_id = ?1 AND a.resolution IN ('captured','accepted') ORDER BY a.id DESC LIMIT 1",
                        [&rec],
                        |r| Ok((r.get(0)?, r.get(1)?)),
                    )
                    .ok();
                Ok(ReviewItem { id, reason, opened, record: self.record(&rec)?, approved })
            })
            .collect()
    }

    pub fn review_show(&self, record_id: &str) -> Result<ReviewItem> {
        self.review_list()?.into_iter().find(|i| i.record.id == record_id).ok_or_else(|| anyhow::anyhow!("{record_id} has no open review"))
    }

    /// Human accepts the current anchor (and, for `changed`, the edited text).
    pub fn review_accept(&self, record_id: &str) -> Result<()> {
        let r = self.record(record_id)?;
        let (Some(s), Some(e)) = (r.start, r.end) else {
            bail!("{record_id} is {}: no position to accept; retire it and re-capture", r.resolution)
        };
        if r.source_revision_id != r.latest_rev_id {
            bail!("{record_id} anchor is stale; run `orbit-rag sync` first");
        }
        let tx = self.db.unchecked_transaction()?;
        tx.execute(
            "INSERT INTO anchors(record_id, source_revision_id, exact, prefix, suffix, start_offset, end_offset, heading_path, resolution)
             SELECT record_id, source_revision_id, exact, prefix, suffix, start_offset, end_offset, heading_path, 'accepted' FROM anchors WHERE id = ?1",
            [r.anchor_id],
        )?;
        self.revise(record_id, |x| {
            x.status = "active".into();
            x.authority = "human_approved".into();
            x.body = x.exact.clone();
        })?;
        self.close_review(record_id, "accepted")?;
        self.index_record(record_id)?;
        self.event("review.accepted", record_id, json!({ "anchor": r.anchor_id, "start": s, "end": e }))?;
        tx.commit()?;
        Ok(())
    }

    pub fn retire(&self, record_id: &str) -> Result<()> {
        let tx = self.db.unchecked_transaction()?;
        self.revise(record_id, |x| x.status = "retired".into())?;
        self.close_review(record_id, "retired")?;
        self.event("record.retired", record_id, json!({}))?;
        tx.commit()?;
        Ok(())
    }

    fn close_review(&self, record_id: &str, state: &str) -> Result<()> {
        self.db.execute(
            "UPDATE review_items SET state = ?2, resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE record_id = ?1 AND state = 'open'",
            params![record_id, state],
        )?;
        Ok(())
    }

    pub fn link(&self, src: &str, kind: &str, dst: &str, provenance: &str) -> Result<()> {
        if !EDGE_KINDS.contains(&kind) {
            bail!("edge kind must be one of {}", EDGE_KINDS.join(", "));
        }
        self.record(src)?;
        self.record(dst)?;
        let tx = self.db.unchecked_transaction()?;
        tx.execute("INSERT OR IGNORE INTO edges(src, kind, dst, provenance) VALUES (?1, ?2, ?3, ?4)", params![src, kind, dst, provenance])?;
        if kind == "supersedes" {
            self.revise(dst, |x| x.status = "superseded".into())?;
        }
        self.event("edge.added", src, json!({ "kind": kind, "dst": dst }))?;
        tx.commit()?;
        Ok(())
    }

    pub fn edges_of(&self, id: &str) -> Result<Vec<Edge>> {
        Ok(self
            .db
            .prepare("SELECT src, kind, dst, provenance FROM edges WHERE src = ?1 OR dst = ?1 ORDER BY id")?
            .query_map([id], |r| Ok(Edge { src: r.get(0)?, kind: r.get(1)?, dst: r.get(2)?, provenance: r.get(3)? }))?
            .collect::<rusqlite::Result<_>>()?)
    }

    /// Records anchored in `source_id`, then (breadth-first, depth-limited, cycle-safe)
    /// records that depend on / implement / are supported by them. Reports only:
    /// nothing downstream is invalidated automatically.
    pub fn impact(&self, source_id: &str, max_depth: usize) -> Result<Vec<Impact>> {
        let mut out: Vec<Impact> = self.records_where("source_id = ?1", [source_id])?.into_iter().map(|record| Impact { record, depth: 0, via: None }).collect();
        let mut seen: HashSet<String> = out.iter().map(|i| i.record.id.clone()).collect();
        let mut frontier: Vec<String> = seen.iter().cloned().collect();
        for depth in 1..=max_depth {
            let mut next = Vec::new();
            for id in &frontier {
                let mut st = self.db.prepare("SELECT src, kind FROM edges WHERE dst = ?1 AND kind IN ('depends_on','implements','supports')")?;
                for row in st.query_map([id], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))? {
                    let (src, kind) = row?;
                    if seen.insert(src.clone()) {
                        out.push(Impact { record: self.record(&src)?, depth, via: Some(format!("{src} {kind} {id}")) });
                        next.push(src);
                    }
                }
            }
            frontier = next;
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::decision_passage;

    #[test]
    fn finds_the_decision_statement() {
        let p = |t: &str| decision_passage(t).map(|(s, e)| t[s..e].to_string());
        let bullets = "---\ntype: decision\n---\n\n# TECH-020: x\n\n- **Date:** 2026 · **Status:** active.\n- **Insight:** measured.\n- **Decision:** One lot rule.\n  Continued.\n- **Rejected:** y.\n";
        assert_eq!(p(bullets).unwrap(), "- **Decision:** One lot rule.\n  Continued.");
        let adr = "# ADR-006: Lower the limit\n\nStatus: accepted\n\n## Context\n\nOutage.\n\n## Decision\n\nFrom 3.3 the limit is 90.\n\n## Consequences\n\nSlower.\n";
        assert_eq!(p(adr).unwrap(), "From 3.3 the limit is 90.");
        let plain = "# Paid data spend follows payment\n\n**Status:** decided 2026-10-03\n\nWe only pay for data once a user pays.\n";
        assert_eq!(p(plain).unwrap(), "We only pay for data once a user pays.");
        assert_eq!(p("# Title only\n"), None);
        let nested = "# Canada ACB\n\n- **Decision:**\n  - `ca.rs::match_ca`: per asset.\n  - A new variant.\n- **Rejected:**\n  - FIFO.\n";
        assert_eq!(p(nested).unwrap(), "- **Decision:**\n  - `ca.rs::match_ca`: per asset.\n  - A new variant.");
        assert!(super::is_template("- **Decision:** <what we will do / stop doing>") && !super::is_template("use `Vec<u8>` here"));
    }
}
