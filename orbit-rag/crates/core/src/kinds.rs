//! What kind of document a source is, from conventions any repo may follow (no
//! repo-specific paths): frontmatter (`type:`, `status:`), ADR names, decision
//! folders, status lines, dated and log-style files, agent instruction files,
//! marketing folders. Ranking uses it as a prior; hits carry it as a label.
//! A repo can add rules and weights in `.orbit-rag/kinds.json`:
//! `{ "rules": [["path/prefix/", "decision"]], "weights": { "agent": 0.5 } }`.
use serde::Deserialize;
use std::collections::HashMap;
use std::path::Path;

pub const KINDS: &[&str] = &["decision", "spec", "doc", "code", "history", "agent", "marketing"];

/// Prior multiplied into a hit's fused score (set before evaluation, tuned on the tune split only).
fn default_weight(kind: &str) -> f64 {
    match kind {
        // Chosen on the tune split of a held-out eval (benchmarks/RESULTS.md §8): boosting
        // decisions and specs helped; also demoting history/agent docs cost more than it gave.
        "decision" => 1.3,
        "spec" => 1.15,
        _ => 1.0,
    }
}

#[derive(Debug, Default, Clone, Deserialize)]
pub struct Overrides {
    #[serde(default)]
    pub rules: Vec<(String, String)>,
    #[serde(default)]
    pub weights: HashMap<String, f64>,
}

impl Overrides {
    pub fn load(dir: &Path) -> Overrides {
        std::fs::read_to_string(dir.join("kinds.json")).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default()
    }

    pub fn weight(&self, kind: &str) -> f64 {
        if std::env::var("ORBIT_KIND_PRIORS").as_deref() == Ok("0") {
            return 1.0;
        }
        self.weights.get(kind).copied().unwrap_or_else(|| default_weight(kind))
    }

    pub fn classify(&self, path: &str, text: &str) -> &'static str {
        if let Some((_, k)) = self.rules.iter().find(|(p, _)| path.starts_with(p.as_str())) {
            if let Some(k) = KINDS.iter().find(|x| **x == k.as_str()) {
                return k;
            }
        }
        classify(path, text)
    }
}

const HISTORY_DIRS: &[&str] = &[
    "investigations", "investigation", "incidents", "postmortems", "post-mortems", "meetings", "meeting-notes", "minutes", "reports", "retros",
    "retrospectives", "progress", "logs", "journal", "archive", "archived", "plans", "work-log", "worklog", "changelogs", "history", "handovers",
];
const HISTORY_FILES: &[&str] = &["changelog", "changes", "history", "handover", "work-report", "worklog", "work-log", "release-notes", "progress log"];
const AGENT_FILES: &[&str] = &["agents.md", "claude.md", "gemini.md", "skill.md", "copilot-instructions.md", ".cursorrules"];
const MARKETING_DIRS: &[&str] = &["marketing", "emails", "email", "campaigns", "blog", "press", "social", "newsletters", "ads"];
const DOC_ROOTS: &[&str] = &["docs", "doc", "documentation", "spec", "specs", "design"];

/// Frontmatter `key: value` pairs (lowercased), when the file opens with `---`.
fn frontmatter(text: &str) -> HashMap<String, String> {
    let mut out = HashMap::new();
    let mut lines = text.lines();
    if lines.next().map(str::trim) != Some("---") {
        return out;
    }
    for l in lines.take(40) {
        if l.trim() == "---" {
            break;
        }
        if let Some((k, v)) = l.split_once(':') {
            out.insert(k.trim().to_lowercase(), v.trim().trim_matches(['"', '\'']).to_lowercase());
        }
    }
    out
}

/// A `Status:` line near the top (`Status: accepted`, `- **Status:** superseded by ...`).
fn status_line(text: &str) -> Option<String> {
    text.lines().take(40).find_map(|l| {
        let t = l.replace("**", "").to_lowercase();
        let i = t.find("status:")?;
        t[i + 7..].split(|c: char| !c.is_alphanumeric() && c != '-').find(|w| !w.is_empty()).map(String::from)
    })
}

fn is_dated(name: &str) -> bool {
    let b = name.as_bytes();
    b.len() >= 10 && b[..4].iter().all(u8::is_ascii_digit) && b[4] == b'-' && b[5..7].iter().all(u8::is_ascii_digit) && b[7] == b'-' && b[8..10].iter().all(u8::is_ascii_digit)
}

pub fn classify(path: &str, text: &str) -> &'static str {
    if crate::code::language(path).is_some() {
        return "code";
    }
    let lower = path.to_lowercase();
    let segs: Vec<&str> = lower.split('/').collect();
    let (dirs, file) = segs.split_at(segs.len().saturating_sub(1));
    let file = file.first().copied().unwrap_or("");
    let stem = file.rsplit_once('.').map_or(file, |x| x.0);
    let fm = frontmatter(text);
    let status = fm.get("status").cloned().or_else(|| status_line(text)).unwrap_or_default();
    let ty = fm.get("type").cloned().unwrap_or_default();
    let closed = ["superseded", "deprecated", "rejected", "withdrawn", "obsolete", "archived", "replaced"].contains(&status.as_str());

    let decision_place = dirs.iter().any(|d| d.contains("decision") || *d == "adr" || *d == "adrs")
        || stem.starts_with("adr-")
        || stem.starts_with("adr_")
        || (stem.starts_with("adr") && stem[3..].starts_with(|c: char| c.is_ascii_digit()));
    if ty == "decision" || ty == "adr" || decision_place {
        return if closed { "history" } else { "decision" };
    }
    if dirs.first().is_some_and(|d| d.starts_with('.') && *d != ".github") || AGENT_FILES.contains(&file) || dirs.iter().any(|d| *d == "skills" || *d == "prompts") {
        return "agent";
    }
    if closed
        || ["progress", "log", "meeting", "report", "investigation", "journal", "incident", "retro", "handover"].contains(&ty.as_str())
        || dirs.iter().any(|d| HISTORY_DIRS.contains(d))
        || HISTORY_FILES.iter().any(|h| stem == *h || stem.starts_with(&format!("{h}-")) || stem.starts_with(&format!("{h} ")))
        || is_dated(stem)
    {
        return "history";
    }
    if dirs.iter().any(|d| MARKETING_DIRS.contains(d) || d.ends_with(" marketing")) {
        return "marketing";
    }
    if dirs.first().is_some_and(|d| DOC_ROOTS.contains(d)) || ["readme", "architecture", "spec", "design", "contributing"].contains(&stem) {
        return "spec";
    }
    "doc"
}

#[cfg(test)]
mod tests {
    use super::classify;

    #[test]
    fn generic_conventions() {
        let c = |p: &str, t: &str| classify(p, t);
        assert_eq!(c("docs/adr/ADR-004-state.md", "# ADR-004\n\nStatus: accepted\n"), "decision");
        assert_eq!(c("docs/adr/ADR-001-redux.md", "# ADR-001\n\nStatus: superseded by ADR-004\n"), "history");
        assert_eq!(c("notes/06 Decisions/2026-09-24 The Move.md", "---\ntype: decision\nstatus: active\n---\n# T\n"), "decision");
        assert_eq!(c("notes/2026-09-24 standup.md", "# standup\n"), "history");
        assert_eq!(c("docs/investigations/2026-09-23-bug.md", "# x\n"), "history");
        assert_eq!(c("CHANGELOG.md", "# Changelog\n"), "history");
        assert_eq!(c(".agents/skills/debug/SKILL.md", "# skill\n"), "agent");
        assert_eq!(c("AGENTS.md", "# agents\n"), "agent");
        assert_eq!(c("emails/loops/08-cost-basis.md", "# email\n"), "marketing");
        assert_eq!(c("docs/positions/tax-calculation.md", "# Tax\n"), "spec");
        assert_eq!(c("README.md", "# r\n"), "spec");
        assert_eq!(c("notes/ideas.md", "# i\n"), "doc");
        assert_eq!(c("src/lib.rs", "fn x() {}"), "code");
        assert_eq!(c("design/rfc-12.md", "- **Date:** 2026 · **Status:** rejected.\n"), "history");
    }
}
