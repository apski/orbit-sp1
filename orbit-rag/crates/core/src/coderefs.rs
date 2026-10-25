//! "Implemented in": names a doc passage puts in backticks (`bridge_cost_basis_move`,
//! `Store::reindex`, `ORBIT_EMBED`, `src/api/client.ts`) resolved to where the code
//! defines them (code chunks' symbols, plus constant and env-read tags). Pointers only:
//! code stays out of the results.
use crate::retrieval::{CodeRef, Hit};
use crate::storage::{line_of, Store};
use anyhow::Result;
use std::collections::HashMap;

const MAX_REFS: usize = 6;

/// Backticked spans that look like code: a path with an extension, or an identifier
/// with `_`, an inner capital, or a `::`/`.` qualifier (plain words like `after` don't
/// count). Most specific first: paths, then qualified names, then bare identifiers.
/// Each comes with its qualifier (`stages.rs` in `stages.rs::subtract_remaining`) as a hint.
pub fn names(text: &str) -> Vec<(String, Option<String>)> {
    let mut out: Vec<(u8, String, Option<String>)> = Vec::new();
    for (i, span) in text.split('`').enumerate() {
        if i % 2 == 0 || span.len() > 120 || span.contains(' ') {
            continue;
        }
        let s = span.trim_end_matches("()").trim_end_matches(';');
        let is_path = s.contains('/') && s.rsplit('/').next().is_some_and(|f| f.contains('.'));
        let qualified = s.contains("::") || (s.contains('.') && !is_path);
        let last = s.rsplit("::").next().unwrap_or(s).rsplit('.').next().unwrap_or(s);
        let ident = last.chars().all(|c| c.is_alphanumeric() || c == '_') && last.chars().next().is_some_and(|c| c.is_alphabetic() || c == '_');
        let codey = last.contains('_') || last.chars().skip(1).any(char::is_uppercase) || qualified;
        let hint = qualified.then(|| s[..s.len() - last.len()].trim_end_matches([':', '.']).rsplit("::").next().unwrap_or("").to_string()).filter(|h| !h.is_empty());
        let (rank, name) = if is_path { (0, s.to_string()) } else if ident && codey && last.len() >= 4 { (if qualified { 1 } else { 2 }, last.to_string()) } else { continue };
        if !out.iter().any(|o| o.1 == name) {
            out.push((rank, name, hint));
        }
    }
    out.sort_by_key(|o| o.0); // stable: text order within a rank
    out.into_iter().map(|(_, n, h)| (n, h)).collect()
}

fn resolve(store: &Store, name: &str, hint: Option<&str>, snaps: &mut HashMap<String, String>) -> Result<Vec<CodeRef>> {
    if name.contains('/') {
        let mut st = store.db.prepare("SELECT path FROM sources WHERE path = ?1 OR path LIKE '%/' || ?1 LIMIT 1")?;
        return Ok(st.query_map([name], |r| r.get::<_, String>(0))?.filter_map(|p| p.ok()).map(|path| CodeRef { name: name.into(), path, line: 1 }).collect());
    }
    // definitions (every symbol is a chunk), then constants and env reads from the code tags
    let mut st = store.db.prepare(
        "SELECT * FROM (SELECT s.path, c.start_offset, r.snapshot_hash FROM chunks c
           JOIN source_revisions r ON r.id = c.revision_id JOIN sources s ON s.id = r.source_id
           WHERE c.symbol = ?1 AND c.lang <> 'markdown' ORDER BY instr(s.path, ?2) = 0, s.path LIMIT 1)
         UNION ALL
         SELECT * FROM (SELECT s.path, t.start_offset, r.snapshot_hash FROM tags t JOIN chunks c ON c.id = t.chunk_id
           JOIN source_revisions r ON r.id = c.revision_id JOIN sources s ON s.id = r.source_id
           WHERE t.kind IN ('constant', 'config') AND t.name = ?1 ORDER BY instr(s.path, ?2) = 0, s.path LIMIT 1)
         LIMIT 1",
    )?;
    let rows: Vec<(String, usize, String)> = st.query_map([name, hint.unwrap_or("\u{1}")], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect::<rusqlite::Result<_>>()?;
    rows.into_iter()
        .map(|(path, at, snap)| {
            if !snaps.contains_key(&snap) {
                snaps.insert(snap.clone(), store.snapshot(&snap)?);
            }
            Ok(CodeRef { name: name.into(), line: line_of(&snaps[&snap], at), path })
        })
        .collect()
}

/// Fill `code_refs` on every non-code hit.
pub fn attach(store: &Store, hits: &mut [Hit]) -> Result<()> {
    let mut snaps = HashMap::new();
    let mut cache: HashMap<String, Vec<CodeRef>> = HashMap::new();
    for h in hits.iter_mut().filter(|h| h.kind != "code") {
        let text = h.context.clone().unwrap_or_else(|| h.text.clone());
        for (n, hint) in names(&text) {
            let key = format!("{n}\0{}", hint.as_deref().unwrap_or(""));
            if !cache.contains_key(&key) {
                let r = resolve(store, &n, hint.as_deref(), &mut snaps)?;
                cache.insert(key.clone(), r);
            }
            for r in &cache[&key] {
                if h.code_refs.len() < MAX_REFS && r.path != h.path && !h.code_refs.iter().any(|x| x.path == r.path && x.line == r.line) {
                    h.code_refs.push(r.clone());
                }
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    #[test]
    fn only_code_looking_names() {
        let t = "Use `bridge_cost_basis_move` in `stages.rs::subtract_remaining`, read `ORBIT_EMBED`, see `src/api/client.ts`, call `Store::reindex()`; not `after`, `limit`, `a b`, or `Redux`.";
        let n: Vec<String> = super::names(t).into_iter().map(|(n, h)| match h { Some(h) => format!("{h}::{n}"), None => n }).collect();
        assert_eq!(n, ["src/api/client.ts", "stages.rs::subtract_remaining", "Store::reindex", "bridge_cost_basis_move", "ORBIT_EMBED"]);
    }
}
