use anyhow::{bail, Context, Result};
use rusqlite::{params, Connection, OptionalExtension, Row};
use serde::Serialize;
use serde_json::Value;
use std::path::{Path, PathBuf};

const MIGRATIONS: &[&str] = &[
    include_str!("../../../migrations/0001_init.sql"),
    include_str!("../../../migrations/0002_code_and_diffs.sql"),
    include_str!("../../../migrations/0003_embedding_ids.sql"),
    include_str!("../../../migrations/0004_code_toggle_and_tags.sql"),
    include_str!("../../../migrations/0005_lookup_indexes.sql"),
    include_str!("../../../migrations/0006_chunk_kinds.sql"),
    include_str!("../../../migrations/0007_chunk_symbols.sql"),
    include_str!("../../../migrations/0008_events_by_entity.sql"),
];

pub struct Store {
    pub db: Connection,
    pub root: PathBuf,
    /// Per-repo kind rules and weights (`.orbit-rag/kinds.json`), else the defaults.
    pub kinds: crate::kinds::Overrides,
}

/// The per-repo data directory. Not `.orbit/`: orbit-SP1's Orbit owns that.
pub const DATA_DIR: &str = ".orbit-rag";
/// Where stores lived before the rename; moved on first open.
const LEGACY_DIR: &str = ".orbit";
const LEGACY_FILES: &[&str] = &["orbit.db", "orbit.db-wal", "orbit.db-shm", "lance", "ann", "backups"];

impl Store {
    pub fn init(root: &Path) -> Result<Store> {
        std::fs::create_dir_all(root.join(DATA_DIR))?;
        Self::open_at(root)
    }

    /// Find `.orbit-rag/` in `start` or a parent, like git. A pre-rename store
    /// (`.orbit/orbit.db`) is moved into `.orbit-rag/` first.
    pub fn discover(start: &Path) -> Result<Store> {
        let start = start.canonicalize()?;
        for dir in start.ancestors() {
            if !dir.join(DATA_DIR).is_dir() && dir.join(LEGACY_DIR).join("orbit.db").is_file() {
                migrate_legacy_dir(dir)?;
            }
            if dir.join(DATA_DIR).is_dir() {
                return Self::open_at(dir);
            }
        }
        bail!("not inside an orbit-rag repository (run `orbit-rag init` at the repo root)")
    }

    pub fn dir(&self) -> PathBuf {
        self.root.join(DATA_DIR)
    }

    fn open_at(root: &Path) -> Result<Store> {
        let root = root.canonicalize()?;
        let db = Connection::open(root.join(DATA_DIR).join("orbit.db"))?;
        db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;")?;
        let v: usize = db.query_row("PRAGMA user_version", [], |r| r.get(0))?;
        for (i, sql) in MIGRATIONS.iter().enumerate().skip(v) {
            let tx = db.unchecked_transaction()?;
            tx.execute_batch(sql).with_context(|| format!("migration {}", i + 1))?;
            tx.pragma_update(None, "user_version", i + 1)?;
            tx.commit()?;
        }
        let kinds = crate::kinds::Overrides::load(&root.join(DATA_DIR));
        let store = Store { db, root, kinds };
        if v > 0 && v < MIGRATIONS.len() && v < 6 {
            store.reindex()?; // 0004 added code tags, 0006 chunk kinds: derive them for existing sources
        }
        Ok(store)
    }

    /// Validate a path against the registered root: no traversal, no symlink
    /// escape, Markdown only. Returns the repo-relative path.
    pub fn rel_path(&self, abs_or_rel: &Path) -> Result<String> {
        let p = if abs_or_rel.is_absolute() { abs_or_rel.to_path_buf() } else { self.root.join(abs_or_rel) };
        let canon = p.canonicalize().with_context(|| format!("no such file: {}", p.display()))?;
        let rel = canon.strip_prefix(&self.root).map_err(|_| anyhow::anyhow!("{} is outside {}", canon.display(), self.root.display()))?;
        if !crate::sources::supported(&canon.to_string_lossy()) || rel.starts_with(DATA_DIR) || rel.starts_with(LEGACY_DIR) || !canon.is_file() {
            bail!("not a supported source (Markdown, rs, ts/tsx, js/jsx, py): {}", rel.display());
        }
        Ok(rel.to_string_lossy().replace('\\', "/"))
    }

    pub fn event(&self, kind: &str, entity: &str, data: Value) -> Result<()> {
        self.db.execute("INSERT INTO events(kind, entity, data) VALUES (?1, ?2, ?3)", params![kind, entity, data.to_string()])?;
        Ok(())
    }

    pub fn next_id(&self, table: &str, prefix: &str) -> Result<String> {
        let n: i64 = self.db.query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |r| r.get(0))?;
        Ok(format!("{prefix}_{:03}", n + 1))
    }

    pub fn snapshot(&self, hash: &str) -> Result<String> {
        Ok(self.db.query_row("SELECT content FROM snapshots WHERE hash = ?1", [hash], |r| r.get(0))?)
    }

    pub fn record(&self, id: &str) -> Result<Record> {
        self.db
            .query_row("SELECT * FROM current_records WHERE id = ?1", [id], Record::from_row)
            .optional()?
            .with_context(|| format!("no such record: {id}"))
    }

    pub fn records_where(&self, filter: &str, args: impl rusqlite::Params) -> Result<Vec<Record>> {
        let mut st = self.db.prepare(&format!("SELECT * FROM current_records WHERE {filter} ORDER BY id"))?;
        let rows = st.query_map(args, Record::from_row)?.collect::<rusqlite::Result<_>>()?;
        Ok(rows)
    }

    /// Append an immutable record revision derived from the current one.
    pub fn revise(&self, id: &str, f: impl FnOnce(&mut Record)) -> Result<()> {
        let mut r = self.record(id)?;
        f(&mut r);
        self.db.execute(
            "INSERT INTO record_revisions(record_id, rev, category, body, note, scope, status, authority)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![id, r.rev + 1, r.category, r.body, r.note, r.scope, r.status, r.authority],
        )?;
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct Record {
    pub id: String,
    pub rev: i64,
    pub category: String,
    pub body: String,
    pub note: Option<String>,
    pub scope: String,
    pub status: String,
    pub authority: String,
    pub anchor_id: i64,
    pub source_revision_id: i64,
    pub exact: String,
    pub prefix: String,
    pub suffix: String,
    pub start: Option<usize>,
    pub end: Option<usize>,
    pub heading_path: Vec<String>,
    pub resolution: String,
    pub source_id: String,
    pub path: String,
    pub snapshot_hash: String,
    pub git_rev: Option<String>,
    pub latest_rev_id: i64,
}

impl Record {
    fn from_row(r: &Row) -> rusqlite::Result<Record> {
        let hp: String = r.get("heading_path")?;
        Ok(Record {
            id: r.get("id")?,
            rev: r.get("rev")?,
            category: r.get("category")?,
            body: r.get("body")?,
            note: r.get("note")?,
            scope: r.get("scope")?,
            status: r.get("status")?,
            authority: r.get("authority")?,
            anchor_id: r.get("anchor_id")?,
            source_revision_id: r.get("source_revision_id")?,
            exact: r.get("exact")?,
            prefix: r.get("prefix")?,
            suffix: r.get("suffix")?,
            start: r.get("start_offset")?,
            end: r.get("end_offset")?,
            heading_path: serde_json::from_str(&hp).unwrap_or_default(),
            resolution: r.get("resolution")?,
            source_id: r.get("source_id")?,
            path: r.get("path")?,
            snapshot_hash: r.get("snapshot_hash")?,
            git_rev: r.get("git_rev")?,
            latest_rev_id: r.get("latest_rev_id")?,
        })
    }

    pub fn confident(&self) -> bool {
        matches!(self.resolution.as_str(), "captured" | "exact" | "relocated" | "accepted")
    }

    /// Freshness/authority as one label. Only "current" may be treated as authoritative
    /// (and then only together with authority = human_approved).
    pub fn validity(&self) -> &'static str {
        match self.status.as_str() {
            "retired" => "retired",
            "superseded" => "superseded",
            "needs_review" => "needs_review",
            _ if self.source_revision_id != self.latest_rev_id => "stale",
            _ if !self.confident() => "needs_review",
            _ => "current",
        }
    }

    pub fn authoritative(&self) -> bool {
        self.validity() == "current" && self.authority == "human_approved"
    }

    pub fn uri(&self) -> String {
        format!("repo://{}", self.path)
    }

    pub fn revision_label(&self) -> String {
        match &self.git_rev {
            Some(g) => format!("git:{g}"),
            None => format!("snap:{}", &self.snapshot_hash[..12]),
        }
    }
}

pub fn line_of(text: &str, byte: usize) -> usize {
    text[..byte.min(text.len())].matches('\n').count() + 1
}

pub fn utf16_to_byte(s: &str, u: usize) -> Option<usize> {
    let mut n = 0;
    for (b, c) in s.char_indices() {
        if n == u {
            return Some(b);
        }
        n += c.len_utf16();
        if n > u {
            return None; // inside a surrogate pair
        }
    }
    (n == u).then_some(s.len())
}

/// Move a pre-rename store out of `.orbit/` (which orbit-SP1's Orbit may share) into
/// `.orbit-rag/`, file by file, and drop `.orbit/` only if nothing else is left.
fn migrate_legacy_dir(root: &Path) -> Result<()> {
    let (old, new) = (root.join(LEGACY_DIR), root.join(DATA_DIR));
    std::fs::create_dir_all(&new)?;
    for f in LEGACY_FILES {
        if old.join(f).exists() {
            std::fs::rename(old.join(f), new.join(f)).with_context(|| format!("moving {} to {}", old.join(f).display(), new.display()))?;
        }
    }
    let _ = std::fs::remove_dir(&old); // fails (and is ignored) when not empty
    eprintln!("moved the index from {} to {}", old.display(), new.display());
    Ok(())
}

pub fn byte_to_utf16(s: &str, b: usize) -> usize {
    s[..b].encode_utf16().count()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn legacy_store_moves_out_of_a_shared_orbit_dir() {
        let base = std::env::temp_dir().join(format!("orbit-legacy-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(base.join(DATA_DIR)).unwrap();
        std::fs::write(base.join("a.md"), "# a\n").unwrap();
        let s = Store::open_at(&base).unwrap();
        s.open_source(&base.join("a.md")).unwrap();
        drop(s);
        // pre-rename layout, sharing .orbit/ with someone else's file
        std::fs::rename(base.join(DATA_DIR), base.join(LEGACY_DIR)).unwrap();
        std::fs::write(base.join(LEGACY_DIR).join("orbit.toml"), "x").unwrap();
        let s = Store::discover(&base).unwrap();
        assert_eq!(s.source_files().unwrap(), ["a.md"]);
        assert!(base.join(DATA_DIR).join("orbit.db").is_file() && !base.join(LEGACY_DIR).join("orbit.db").exists());
        assert!(base.join(LEGACY_DIR).join("orbit.toml").is_file(), "other tools' files stay put");
        std::fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn rel_path_rejects_escapes_and_unsupported_files() {
        let base = std::env::temp_dir().join(format!("orbit-relpath-{}", std::process::id()));
        let (root, outside) = (base.join("repo"), base.join("outside.md"));
        std::fs::create_dir_all(root.join("docs")).unwrap();
        std::fs::write(&outside, "# secret").unwrap();
        std::fs::write(root.join("docs/ok.md"), "# ok").unwrap();
        std::fs::write(root.join("docs/x.txt"), "no").unwrap();
        std::os::unix::fs::symlink(&outside, root.join("docs/link.md")).unwrap();
        let s = Store::init(&root).unwrap();
        assert_eq!(s.rel_path(Path::new("docs/ok.md")).unwrap(), "docs/ok.md");
        for bad in ["docs/link.md", "../outside.md", "docs/../../outside.md", "docs/x.txt", ".orbit-rag/orbit.db", ".orbit/orbit.db", "docs/missing.md"] {
            assert!(s.rel_path(Path::new(bad)).is_err(), "{bad} should be rejected");
        }
        assert!(s.rel_path(&outside).is_err());
        std::fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn utf16_offsets_round_trip() {
        let s = "a🚀ż b";
        for (b, _) in s.char_indices().chain([(s.len(), ' ')]) {
            assert_eq!(utf16_to_byte(s, byte_to_utf16(s, b)), Some(b));
        }
        assert_eq!(utf16_to_byte(s, 2), None); // inside the surrogate pair
    }
}
