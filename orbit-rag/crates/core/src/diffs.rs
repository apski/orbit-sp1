//! Git diff hunks as a retrieval corpus: evidence of *what changed*, never of
//! current truth (validity "historical").
use crate::sources::{index_text, sha256, supported};
use crate::storage::Store;
use anyhow::{bail, Result};
use rusqlite::params;
use std::process::Command;

const MAX_HUNK_BYTES: usize = 4000;

#[derive(Debug, PartialEq)]
pub struct Hunk {
    pub path: String,
    pub old_start: usize,
    pub old_lines: usize,
    pub new_start: usize,
    pub new_lines: usize,
    pub text: String,
}

/// Parse `git diff` unified output (new-side path; deletions keep the old path).
pub fn parse(diff: &str) -> Vec<Hunk> {
    let range = |s: &str| -> (usize, usize) {
        let (a, b) = s.split_once(',').unwrap_or((s, "1"));
        (a.parse().unwrap_or(0), b.parse().unwrap_or(1))
    };
    let (mut out, mut path, mut old_path) = (Vec::<Hunk>::new(), String::new(), String::new());
    for line in diff.lines() {
        if let Some(p) = line.strip_prefix("--- ") {
            old_path = p.strip_prefix("a/").unwrap_or(p).to_string();
        } else if let Some(p) = line.strip_prefix("+++ ") {
            path = if p == "/dev/null" { old_path.clone() } else { p.strip_prefix("b/").unwrap_or(p).to_string() };
        } else if line.starts_with("diff --git") {
            path.clear();
        } else if let Some(h) = line.strip_prefix("@@ -") {
            let mut it = h.split(' ');
            let (os, ol) = range(it.next().unwrap_or("0"));
            let (ns, nl) = range(it.next().unwrap_or("+0").trim_start_matches('+'));
            out.push(Hunk { path: path.clone(), old_start: os, old_lines: ol, new_start: ns, new_lines: nl, text: format!("{line}\n") });
        } else if let Some(h) = out.last_mut().filter(|_| !path.is_empty()) {
            if h.text.len() < MAX_HUNK_BYTES {
                h.text.push_str(line);
                h.text.push('\n');
            }
        }
    }
    out
}

impl Store {
    fn git(&self, args: &[&str]) -> Result<String> {
        let o = Command::new("git").arg("-C").arg(&self.root).args(args).output()?;
        if !o.status.success() {
            bail!("git {}: {}", args.join(" "), String::from_utf8_lossy(&o.stderr).trim());
        }
        Ok(String::from_utf8_lossy(&o.stdout).into_owned())
    }

    /// Replace the indexed hunks for `base..head` (head may be "worktree").
    pub fn index_diffs(&self, base: &str, head: &str) -> Result<usize> {
        let b = self.git(&["rev-parse", "--short=12", base])?.trim().to_string();
        let (h, diff) = if head == "worktree" {
            ("worktree".to_string(), self.git(&["diff", "--no-color", "--no-ext-diff", "-U3", &b])?)
        } else {
            let h = self.git(&["rev-parse", "--short=12", head])?.trim().to_string();
            let d = self.git(&["diff", "--no-color", "--no-ext-diff", "-U3", &b, &h])?;
            (h, d)
        };
        let hunks: Vec<Hunk> = parse(&diff).into_iter().filter(|x| supported(&x.path)).collect();
        let tx = self.db.unchecked_transaction()?;
        tx.execute("DELETE FROM diffs_fts WHERE hunk_id IN (SELECT id FROM diff_hunks WHERE base = ?1 AND head = ?2)", params![b, h])?;
        tx.execute("DELETE FROM diff_hunks WHERE base = ?1 AND head = ?2", params![b, h])?;
        for x in &hunks {
            let it = index_text(&["diff".into(), x.path.clone()], &x.text);
            let hash = sha256(&it);
            tx.execute(
                "INSERT OR REPLACE INTO diff_hunks(base, head, path, old_start, old_lines, new_start, new_lines, text, hash, lang) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
                params![b, h, x.path, x.old_start, x.old_lines, x.new_start, x.new_lines, x.text, hash, crate::sources::fence_lang(&x.path)],
            )?;
            let id = tx.last_insert_rowid();
            tx.execute("INSERT INTO diffs_fts(hunk_id, path, text) VALUES (?1, ?2, ?3)", params![id, x.path, x.text])?;
            tx.execute("INSERT OR IGNORE INTO embed_outbox(hash, text) VALUES (?1, ?2)", params![hash, it])?;
        }
        self.event("diffs.indexed", &format!("{b}..{h}"), serde_json::json!({ "hunks": hunks.len() }))?;
        tx.commit()?;
        Ok(hunks.len())
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn parses_hunks_with_sides() {
        let d = "diff --git a/docs/a.md b/docs/a.md\nindex 1..2 100644\n--- a/docs/a.md\n+++ b/docs/a.md\n@@ -3,2 +3,3 @@ ## Nav\n keep\n-old\n+new\n+more\n@@ -10 +11 @@\n-x\n+y\ndiff --git a/gone.md b/gone.md\n--- a/gone.md\n+++ /dev/null\n@@ -1 +0,0 @@\n-bye\n";
        let h = super::parse(d);
        assert_eq!(h.len(), 3);
        assert_eq!((h[0].old_start, h[0].old_lines, h[0].new_start, h[0].new_lines), (3, 2, 3, 3));
        assert!(h[0].text.contains("-old\n+new\n"));
        assert_eq!((h[1].old_start, h[1].old_lines, h[1].new_start), (10, 1, 11));
        assert_eq!((h[2].path.as_str(), h[2].new_lines), ("gone.md", 0));
    }
}
