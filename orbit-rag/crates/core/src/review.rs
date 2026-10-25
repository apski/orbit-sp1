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

impl Store {
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
