//! Positional re-anchoring of text-quote selectors across source revisions.
//! Only `Exact` and `Relocated` are confident; everything else goes to review.
//! Semantic validity (is the decision still true?) is never decided here.

use serde::Serialize;
use similar::{Algorithm, DiffTag, TextDiff};
use std::time::Duration;

pub const CONTEXT_BYTES: usize = 32;

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct Quote {
    pub exact: String,
    pub prefix: String,
    pub suffix: String,
    pub start: usize,
    pub end: usize,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Resolution {
    Exact,          // same offsets, same context
    Relocated,      // same text + context, new offsets
    ContextChanged, // same text, surrounding text changed -> meaning may have changed
    Changed,        // selection itself edited; proposed fuzzy match
    Ambiguous,      // several equally good matches
    Orphaned,       // gone
}

impl Resolution {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Exact => "exact",
            Self::Relocated => "relocated",
            Self::ContextChanged => "context_changed",
            Self::Changed => "changed",
            Self::Ambiguous => "ambiguous",
            Self::Orphaned => "orphaned",
        }
    }
    pub fn confident(self) -> bool {
        matches!(self, Self::Exact | Self::Relocated)
    }
}

#[derive(Debug, PartialEq)]
pub struct Resolved {
    pub resolution: Resolution,
    pub range: Option<(usize, usize)>,
}

pub fn floor_char(s: &str, mut i: usize) -> usize {
    i = i.min(s.len());
    while !s.is_char_boundary(i) {
        i -= 1;
    }
    i
}

pub fn ceil_char(s: &str, mut i: usize) -> usize {
    i = i.min(s.len());
    while !s.is_char_boundary(i) {
        i += 1;
    }
    i
}

/// Context is clipped to the enclosing block (blank line), so edits to a
/// neighbouring paragraph don't count as a context change.
pub fn quote(text: &str, start: usize, end: usize) -> Quote {
    let prefix = &text[floor_char(text, start.saturating_sub(CONTEXT_BYTES))..start];
    let prefix = prefix.rfind("\n\n").map_or(prefix, |i| &prefix[i + 2..]);
    let suffix = &text[end..ceil_char(text, end + CONTEXT_BYTES)];
    let suffix = suffix.find("\n\n").map_or(suffix, |i| &suffix[..i]);
    Quote {
        exact: text[start..end].to_string(),
        prefix: prefix.to_string(),
        suffix: suffix.to_string(),
        start,
        end,
    }
}

fn in_context(text: &str, q: &Quote) -> Vec<usize> {
    text.match_indices(q.exact.as_str())
        .map(|(i, _)| i)
        .filter(|&p| text[..p].ends_with(&q.prefix) && text[p + q.exact.len()..].starts_with(&q.suffix))
        .collect()
}

/// Wider surroundings (crossing blocks) for telling identical copies apart.
const WIDE_BYTES: usize = 256;

fn wide_match(old: &str, new: &str, q: &Quote, p: usize) -> bool {
    let before = &old[floor_char(old, q.start.saturating_sub(WIDE_BYTES))..q.start];
    let after = &old[q.end..ceil_char(old, q.end + WIDE_BYTES)];
    new[..p].ends_with(before) && new[p + q.exact.len()..].starts_with(after)
}

/// Collapse whitespace runs to one space, keeping a byte map back to the original.
fn squash(s: &str) -> (String, Vec<usize>) {
    let (mut out, mut map, mut ws) = (String::new(), Vec::new(), false);
    for (i, c) in s.char_indices() {
        if c.is_whitespace() {
            if !ws {
                out.push(' ');
                map.push(i);
            }
            ws = true;
        } else {
            ws = false;
            out.push(c);
            map.extend(i..i + c.len_utf8());
        }
    }
    (out, map)
}

/// Same words, same context, only whitespace differs (a re-wrapped paragraph).
/// Unique in both revisions, or no answer.
fn whitespace_insensitive(old: &str, new: &str, q: &Quote) -> Option<(usize, usize)> {
    let (ex, pre, suf) = (squash(&q.exact).0, squash(&q.prefix).0, squash(&q.suffix).0);
    let ex = ex.trim();
    if ex.is_empty() {
        return None;
    }
    let found = |t: &str| -> Vec<usize> {
        t.match_indices(ex).map(|(i, _)| i).filter(|&p| t[..p].ends_with(pre.as_str()) && t[p + ex.len()..].starts_with(suf.as_str())).collect()
    };
    let (n, map) = squash(new);
    let hits = found(&n);
    if hits.len() != 1 || found(&squash(old).0).len() > 1 {
        return None;
    }
    Some((map[hits[0]], map[hits[0] + ex.len() - 1] + 1))
}

pub fn resolve(old: &str, new: &str, q: &Quote) -> Resolved {
    let hit = |resolution, p: usize| Resolved { resolution, range: Some((p, p + q.exact.len())) };
    if old == new {
        return hit(Resolution::Exact, q.start);
    }
    let in_ctx = in_context(new, q);
    let ambiguous = Resolved { resolution: Resolution::Ambiguous, range: None };

    if let Some(p) = map_through_diff(old, new, q.start, q.end) {
        // A diff can pick either copy of a newly duplicated passage; trust it only
        // if the wider surroundings single out the same copy (an adjacent paste
        // leaves both copies with changed surroundings -> ambiguous).
        if in_ctx.len() > 1 && in_context(old, q).len() != in_ctx.len() {
            let wide: Vec<usize> = in_ctx.iter().copied().filter(|&c| wide_match(old, new, q, c)).collect();
            if wide != [p] {
                return ambiguous;
            }
        }
        let r = if !in_ctx.contains(&p) {
            Resolution::ContextChanged
        } else if p == q.start {
            Resolution::Exact
        } else {
            Resolution::Relocated
        };
        return hit(r, p);
    }

    let occ: Vec<usize> = new.match_indices(q.exact.as_str()).map(|(i, _)| i).collect();
    match (occ.len(), in_ctx.len()) {
        // one survivor is only "ours" if there was only one to begin with; otherwise
        // the highlighted copy may be exactly the one that was deleted
        (_, 1) if in_context(old, q).len() <= 1 => hit(Resolution::Relocated, in_ctx[0]),
        (1, 0) => hit(Resolution::ContextChanged, occ[0]),
        (0, _) => match whitespace_insensitive(old, new, q) {
            Some(range) => Resolved { resolution: Resolution::Relocated, range: Some(range) },
            None => match fuzzy(new, q) {
                Some(range) => Resolved { resolution: Resolution::Changed, range: Some(range) },
                None => Resolved { resolution: Resolution::Orphaned, range: None },
            },
        },
        _ => ambiguous,
    }
}

/// Map `start` through a line diff; only succeeds when the whole selection
/// sits inside one unchanged run, so `new[result..]` starts with the exact text.
fn map_through_diff(old: &str, new: &str, start: usize, end: usize) -> Option<usize> {
    let diff = TextDiff::configure()
        .algorithm(Algorithm::Patience)
        .timeout(Duration::from_millis(200))
        .diff_lines(old, new);
    let (mut o, mut n) = (0usize, 0usize);
    for op in diff.ops() {
        let (tag, or, nr) = op.as_tag_tuple();
        let ol: usize = diff.old_slices()[or].iter().map(|s| s.len()).sum();
        let nl: usize = diff.new_slices()[nr].iter().map(|s| s.len()).sum();
        if tag == DiffTag::Equal && start >= o && end <= o + ol {
            return Some(n + (start - o));
        }
        o += ol;
        n += nl;
    }
    None
}

// ponytail: windowed levenshtein, O(region_words * len^2); fine for highlights under
// ~500 chars in an 16KB window. Swap for bitap/Myers-approx if long selections get slow.
fn fuzzy(new: &str, q: &Quote) -> Option<(usize, usize)> {
    let want = q.exact.chars().count();
    if want < 12 {
        return None; // too short to propose anything safely
    }
    let lo = floor_char(new, q.start.saturating_sub(8192));
    let hi = ceil_char(new, q.start + q.exact.len() + 8192);
    let region = &new[lo..hi];
    let mut best = (0.0f64, 0usize, 0usize);
    let mut prev_ws = true;
    for (s, c) in region.char_indices() {
        let word_start = prev_ws && !c.is_whitespace();
        prev_ws = c.is_whitespace();
        if !word_start {
            continue;
        }
        for f in [0.8, 0.9, 1.0, 1.1, 1.25] {
            let n = ((want as f64) * f) as usize;
            let e = region[s..].char_indices().nth(n).map(|(i, _)| s + i).unwrap_or(region.len());
            let score = strsim::normalized_levenshtein(&q.exact, &region[s..e]);
            if score > best.0 {
                best = (score, s, e);
            }
        }
    }
    (best.0 >= 0.6).then(|| (lo + best.1, lo + region[best.1..best.2].trim_end().len() + best.1))
}

#[cfg(test)]
mod tests {
    use super::*;
    use Resolution::*;

    const DOC: &str = "# Architecture\n\n## Navigation\n\nArchitecture requires that Native owns navigation while React renders screens.\n\n## Data\n\nThe store is SQLite.\n";
    const SEL: &str = "Native owns navigation";

    fn q() -> Quote {
        let s = DOC.find(SEL).unwrap();
        quote(DOC, s, s + SEL.len())
    }

    /// (name, new text, expected resolution, expected start of `SEL` in new text if any)
    fn cases() -> Vec<(&'static str, String, Resolution, Option<usize>)> {
        let ins = format!("Intro paragraph.\n\n{DOC}");
        let moved = "# Architecture\n\n## Data\n\nThe store is SQLite.\n\n## Navigation\n\nArchitecture requires that Native owns navigation while React renders screens.\n".to_string();
        let dup_then_ins = format!("Note: Native owns navigation (summary).\n\n{DOC}");
        let edited_sel = DOC.replace("Native owns navigation", "Native code owns all navigation");
        let ctx = DOC.replace("Architecture requires that", "Architecture rejects the idea that");
        let deleted = DOC.replace("Architecture requires that Native owns navigation while React renders screens.\n\n", "");
        let p = "Architecture requires that Native owns navigation while React renders screens.";
        let twice = format!("# Notes\n\n{p}\n\n# Other\n\n{p}\n");
        let unicode = format!("Zażółć gęślą jaźń 🚀.\n\n{DOC}");
        let after = DOC.replace("SQLite", "SQLite with WAL");
        vec![
            ("unchanged", DOC.to_string(), Exact, DOC.find(SEL)),
            ("edit after", after.clone(), Exact, after.find(SEL)),
            ("insert above", ins.clone(), Relocated, ins.find(SEL)),
            ("section moved", moved.clone(), Relocated, moved.find(SEL)),
            ("duplicate above + insert", dup_then_ins.clone(), Relocated, dup_then_ins.rfind(SEL)),
            ("unicode above", unicode.clone(), Relocated, unicode.find(SEL)),
            ("selection edited", edited_sel, Changed, None),
            ("context negated", ctx.clone(), ContextChanged, ctx.find(SEL)),
            ("deleted", deleted, Orphaned, None),
            ("pasted twice", twice, Ambiguous, None),
        ]
    }

    #[test]
    fn fixtures_resolve_as_expected_with_no_false_confident_attachment() {
        let q = q();
        for (name, new, want, want_start) in cases() {
            let got = resolve(DOC, &new, &q);
            assert_eq!(got.resolution, want, "{name}");
            if got.resolution.confident() || got.resolution == ContextChanged {
                let (s, e) = got.range.unwrap();
                assert_eq!(&new[s..e], SEL, "{name}: confident range must cover the exact text");
                assert_eq!(Some(s), want_start, "{name}: attached to the wrong occurrence");
            }
        }
    }

    #[test]
    fn preexisting_duplicate_follows_the_diff() {
        let old = "Native owns navigation.\n\nNative owns navigation.\n";
        let s = old.rfind(SEL).unwrap();
        let q = quote(old, s, s + SEL.len());
        let new = format!("Intro.\n\n{old}");
        let r = resolve(old, &new, &q);
        assert_eq!(r.resolution, Relocated);
        assert_eq!(r.range.unwrap().0, new.rfind(SEL).unwrap());
    }

    #[test]
    fn deleting_one_of_two_identical_paragraphs_is_ambiguous() {
        let p = "Architecture requires that Native owns navigation while React renders screens.";
        let old = format!("# A\n\n{p}\n\n# B\n\n{p}\n");
        let s = old.find(SEL).unwrap();
        let q = quote(&old, s, s + SEL.len());
        let new = format!("# B\n\n{p}\n"); // the highlighted (first) copy is gone
        assert_eq!(resolve(&old, &new, &q).resolution, Ambiguous);
    }

    #[test]
    fn far_copy_keeps_original_adjacent_copy_is_ambiguous() {
        let q = q();
        let para = "Architecture requires that Native owns navigation while React renders screens.\n\n";
        let far = format!("{DOC}\n## Appendix\n\n{para}");
        let r = resolve(DOC, &far, &q);
        assert_eq!((r.resolution, r.range.map(|x| x.0)), (Exact, DOC.find(SEL)));
        let adjacent = DOC.replacen(para, &format!("{para}{para}"), 1);
        assert_eq!(resolve(DOC, &adjacent, &q).resolution, Ambiguous);
    }

    #[test]
    fn rewrapped_selection_relocates() {
        let new = DOC.replace("Native owns navigation", "Native owns\nnavigation");
        let r = resolve(DOC, &new, &q());
        let (s, e) = r.range.unwrap();
        assert_eq!((r.resolution, &new[s..e]), (Relocated, "Native owns\nnavigation"));
    }

    #[test]
    fn changed_proposes_the_edited_sentence() {
        let new = DOC.replace("Native owns navigation", "Native code owns all navigation");
        let r = resolve(DOC, &new, &q());
        let (s, e) = r.range.unwrap();
        assert!(new[s..e].contains("owns all navigation"), "{:?}", &new[s..e]);
    }
}
