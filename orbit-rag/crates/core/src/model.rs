//! What the code tags add up to, no model involved:
//! - `code_model`: per subsystem, the types, constants, config knobs, public API,
//!   external boundaries, guards and known ceilings, each with a citation;
//! - `drift`: numeric constants in code compared with the docs that talk about them.
use crate::storage::{line_of, Store};
use crate::tags::subsystem;
use anyhow::Result;
use serde::Serialize;
use std::collections::BTreeMap;

#[derive(Debug, Serialize, Clone)]
pub struct Entry {
    pub kind: String,
    pub name: String,
    pub value: Option<String>,
    pub path: String,
    pub line: usize,
}

#[derive(Debug, Serialize)]
pub struct Subsystem {
    pub name: String,
    pub files: Vec<String>,
    pub entries: Vec<Entry>,
}

fn entries(store: &Store) -> Result<Vec<Entry>> {
    let mut st = store.db.prepare(
        "SELECT t.kind, t.name, t.value, s.path, t.start_offset, r.snapshot_hash FROM tags t JOIN chunks c ON c.id = t.chunk_id
         JOIN source_revisions r ON r.id = c.revision_id JOIN sources s ON s.id = r.source_id ORDER BY s.path, t.start_offset",
    )?;
    let rows: Vec<(String, String, Option<String>, String, usize, String)> =
        st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?)))?.collect::<rusqlite::Result<_>>()?;
    let mut snaps: BTreeMap<String, String> = BTreeMap::new();
    rows.into_iter()
        .map(|(kind, name, value, path, at, snap)| {
            if !snaps.contains_key(&snap) {
                snaps.insert(snap.clone(), store.snapshot(&snap)?);
            }
            Ok(Entry { kind, name, value, line: line_of(&snaps[&snap], at), path })
        })
        .collect()
}

pub fn code_model(store: &Store, scope: Option<&str>) -> Result<Vec<Subsystem>> {
    let mut by: BTreeMap<String, Subsystem> = BTreeMap::new();
    for e in entries(store)? {
        let name = subsystem(&e.path);
        if scope.is_some_and(|s| !name.starts_with(s) && !e.path.starts_with(s)) {
            continue;
        }
        let sub = by.entry(name.clone()).or_insert_with(|| Subsystem { name, files: vec![], entries: vec![] });
        if !sub.files.contains(&e.path) {
            sub.files.push(e.path.clone());
        }
        sub.entries.push(e);
    }
    Ok(by.into_values().collect())
}

pub fn render_model(subs: &[Subsystem]) -> String {
    let mut s = String::from("# Code model\n\nMachine-derived from code (tree-sitter and text scans): a map, not a decision record.\n");
    for sub in subs {
        s += &format!("\n## {}\n\n{}\n", sub.name, sub.files.iter().map(|f| format!("`{f}`")).collect::<Vec<_>>().join(", "));
        let of = |k: &'static str| sub.entries.iter().filter(move |e| e.kind == k);
        let cite = |e: &Entry| format!("{}:{}", e.path, e.line);
        for (k, title) in [("type", "Types"), ("constant", "Constants"), ("config", "Config"), ("api", "Public API"), ("ceiling", "Known ceilings")] {
            let items: Vec<String> = of(k)
                .map(|e| match (&e.value, k) {
                    (Some(v), "type") => format!("- `{}` {{ {v} }} ({})", e.name, cite(e)),
                    (Some(v), "constant") => format!("- `{} = {v}` ({})", e.name, cite(e)),
                    (Some(v), "api") => format!("- `{v}` ({})", cite(e)),
                    (Some(v), _) => format!("- {v} ({})", cite(e)),
                    (None, _) => format!("- `{}` ({})", e.name, cite(e)),
                })
                .collect();
            if !items.is_empty() {
                s += &format!("\n**{title}**\n\n{}\n", items.join("\n"));
            }
        }
        let mut kinds: BTreeMap<&str, usize> = BTreeMap::new();
        of("boundary").for_each(|e| *kinds.entry(e.name.as_str()).or_default() += 1);
        if !kinds.is_empty() {
            s += &format!("\n**Boundaries**: {}\n", kinds.iter().map(|(k, n)| format!("{k} ({n})")).collect::<Vec<_>>().join(", "));
        }
        let checks = of("check").count();
        if checks > 0 {
            s += &format!("\n**Guards**: {checks}\n");
        }
    }
    s
}

#[derive(Debug, Serialize, Clone, PartialEq)]
pub struct Drift {
    pub constant: String,
    pub value: f64,
    pub code: String,
    pub doc: String,
    pub sentence: String,
    pub doc_numbers: Vec<f64>,
    /// "drift": a current doc disagrees; "history": a changelog, incident or
    /// meeting note disagrees, which is expected.
    pub verdict: String,
}

const NUMBER_WORDS: &[&str] = &["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"];
const NAME_NOISE: &[&str] = &["max", "min", "default", "num", "n", "k", "is", "the", "of", "to"];

fn numbers(s: &str) -> Vec<f64> {
    let mut out = Vec::new();
    for w in s.split(|c: char| !(c.is_ascii_alphanumeric() || c == '.' || c == '_')) {
        let w = w.trim_matches('.');
        let digits: String = w.chars().take_while(|c| c.is_ascii_digit() || *c == '.' || *c == '_').filter(|c| *c != '_').collect();
        if let Ok(n) = digits.parse::<f64>() {
            out.push(n);
        } else if let Some(i) = NUMBER_WORDS.iter().position(|n| w.eq_ignore_ascii_case(n)) {
            out.push(i as f64);
        }
    }
    out
}

const UNITS: &[&str] = &[
    "ms", "millisecond", "second", "secs", "minute", "mins", "hour", "day", "week", "month", "year", "kb", "mb", "gb", "byte", "point", "px", "percent",
    "attempt", "retries", "retry", "event", "item", "row", "request", "character", "chars", "token", "chunk", "line", "fps",
];

fn is_unit(w: &str) -> bool {
    UNITS.iter().any(|u| same_word(u, w) || u == &w)
}

/// Sentences: split at ". " and line breaks, never at a decimal point.
fn sentences(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut from = 0;
    let b = text.as_bytes();
    for i in 0..b.len() {
        let end = b[i] == b'\n' || (b[i] == b'.' && b.get(i + 1).is_none_or(|c| c.is_ascii_whitespace()));
        if end {
            out.push(&text[from..=i]);
            from = i + 1;
        }
    }
    if from < text.len() {
        out.push(&text[from..]);
    }
    out
}

/// Lowercased words, each with its value when it is a number ("10%" -> 10, "percent").
fn tokens(s: &str) -> Vec<(String, Option<f64>)> {
    let s = s.replace('%', " percent ");
    s.split(|c: char| !(c.is_alphanumeric() || c == '.' || c == '_'))
        .map(|w| w.trim_matches('.'))
        .filter(|w| !w.is_empty())
        .map(|w| {
            let n = numbers(w).first().copied().filter(|_| w.chars().next().is_some_and(|c| c.is_ascii_digit()) || NUMBER_WORDS.contains(&w.to_lowercase().as_str()));
            (w.to_lowercase(), n)
        })
        .collect()
}

/// Numbers in the sentence that measure what the constant measures. With a unit
/// in the name (`..._MINUTES`), only numbers followed by that unit count; with a
/// rate (`..._PER_MINUTE`), numbers followed within two words by `per <unit>`.
fn quantities(toks: &[(String, Option<f64>)], words: &[String]) -> Vec<f64> {
    let per = words.iter().position(|w| w == "per").and_then(|i| words.get(i + 1));
    let unit = words.last().filter(|w| is_unit(w));
    toks.iter()
        .enumerate()
        .filter_map(|(i, (_, n))| {
            let n = (*n)?;
            let next = |k: usize| toks.get(i + k).map(|t| t.0.as_str()).unwrap_or("");
            let ok = match (per, unit) {
                (Some(p), _) => (1..=3).any(|k| next(k) == "per" && same_word(next(k + 1), p)),
                (None, Some(u)) => same_word(next(1), u) || (next(1) == "by" && same_word(next(3), u)),
                (None, None) => true,
            };
            ok.then_some(n)
        })
        .collect()
}

fn name_words(name: &str) -> Vec<String> {
    let mut words = Vec::new();
    let mut cur = String::new();
    let mut prev_lower = false;
    for c in name.chars() {
        if c == '_' || (c.is_uppercase() && prev_lower) {
            words.push(std::mem::take(&mut cur));
        }
        prev_lower = c.is_lowercase();
        if c != '_' {
            cur.push(c.to_ascii_lowercase());
        }
    }
    words.push(cur);
    words.into_iter().filter(|w| !w.is_empty() && !NAME_NOISE.contains(&w.as_str())).collect()
}

/// `limit`~`limited`, `minute`~`minutes`, `cache`~`cached`: one word extends the other (4+ chars).
fn same_word(a: &str, b: &str) -> bool {
    a == b || (a.len().min(b.len()) >= 4 && (a.starts_with(b) || b.starts_with(a)))
}

fn is_history(path: &str) -> bool {
    let p = path.to_lowercase();
    ["changelog", "history", "incident", "postmortem", "meeting", "retro", "release-notes"].iter().any(|h| p.contains(h))
}

// ponytail: word-overlap matching between a constant's name and a doc sentence;
// misses synonyms ("length" vs "characters"). Measured in `orbit-bench drift`.
pub fn drift(store: &Store) -> Result<Vec<Drift>> {
    let consts: Vec<(Entry, f64, Vec<String>)> = entries(store)?
        .into_iter()
        .filter(|e| e.kind == "constant")
        .filter_map(|e| {
            let n = numbers(e.value.as_deref()?);
            let w = name_words(&e.name);
            (n.len() == 1 && w.len() >= 2).then(|| (e.clone(), n[0], w))
        })
        .collect();
    let mut st = store.db.prepare(
        "SELECT s.path, c.heading_path, c.text, c.start_offset, r.snapshot_hash FROM chunks c JOIN source_revisions r ON r.id = c.revision_id
         JOIN sources s ON s.id = r.source_id WHERE c.lang = 'markdown' ORDER BY s.path, c.start_offset",
    )?;
    let docs: Vec<(String, String, String, usize, String)> = st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)))?.collect::<rusqlite::Result<_>>()?;
    let mut out = Vec::new();
    let mut snaps: BTreeMap<String, String> = BTreeMap::new();
    for (path, heading, text, start, snap) in &docs {
        let heading: Vec<String> = serde_json::from_str(heading).unwrap_or_default();
        let hw: Vec<String> = heading.join(" ").split(|c: char| !c.is_alphanumeric()).map(str::to_lowercase).filter(|w| !w.is_empty()).collect();
        let mut at = 0;
        for sentence in sentences(text) {
            let off = start + at;
            at += sentence.len();
            let toks = tokens(sentence);
            if !toks.iter().any(|t| t.1.is_some()) {
                continue;
            }
            let sw: Vec<&str> = toks.iter().map(|t| t.0.as_str()).chain(hw.iter().map(String::as_str)).collect();
            for (e, v, words) in &consts {
                let named = sentence.contains(e.name.as_str());
                let hit = words.iter().filter(|w| sw.iter().any(|s| same_word(w, s))).count();
                if !named && (hit < 2 || (hit as f64) < 0.6 * words.len() as f64) {
                    continue;
                }
                let nums = quantities(&toks, words);
                if nums.is_empty() || nums.iter().any(|n| (n - v).abs() < 1e-9) {
                    continue; // nothing in this unit, or it agrees
                }
                if !snaps.contains_key(snap) {
                    snaps.insert(snap.clone(), store.snapshot(snap)?);
                }
                out.push(Drift {
                    constant: e.name.clone(),
                    value: *v,
                    code: format!("{}:{}", e.path, e.line),
                    doc: format!("{path}:{}", line_of(&snaps[snap], off)),
                    sentence: sentence.trim().to_string(),
                    doc_numbers: nums,
                    verdict: if is_history(path) { "history" } else { "drift" }.into(),
                });
            }
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn words_and_numbers() {
        assert_eq!(name_words("MAX_SYNC_ATTEMPTS"), ["sync", "attempts"]);
        assert_eq!(name_words("tileCacheMb"), ["tile", "cache", "mb"]);
        assert_eq!(numbers("default 25, maximum 100 and seven days, 1.5 s, 20_000."), [25.0, 100.0, 7.0, 1.5, 20000.0]);
        assert!(same_word("limit", "limited") && same_word("minutes", "minute") && same_word("cache", "cached") && !same_word("mb", "mbps"));
        assert_eq!(sentences("Cold start under 2.5 seconds. Next one.\nLast"), ["Cold start under 2.5 seconds.", " Next one.", "\n", "Last"]);
        let w = |n: &str| name_words(n);
        let q = |s: &str, n: &str| quantities(&tokens(s), &w(n));
        assert_eq!(q("Clients are limited to 120 requests per minute per token.", "TOKEN_TTL_MINUTES"), Vec::<f64>::new());
        assert_eq!(q("Clients are limited to 120 requests per minute per token.", "RATE_LIMIT_PER_MINUTE"), [120.0]);
        assert_eq!(q("Touch targets are at least 44 by 44 points.", "MIN_TOUCH_TARGET_POINTS"), [44.0, 44.0]);
        assert_eq!(q("OTA updates go to 10% of users for 24 hours", "OTA_CANARY_PERCENT"), [10.0]);
    }
}
