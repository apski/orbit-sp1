//! Anchoring benchmark: real paragraphs, seeded edits with ground truth known by
//! construction. Hard gate: zero confident attachments to the wrong place.
use crate::util::{md_files, stats, time, Rng, Stats};
use orbit_core::anchors::{quote, resolve, Resolution};
use orbit_core::sources::blocks;
use serde::Serialize;
use std::collections::BTreeMap;
use std::path::Path;

#[derive(Debug, Clone, Copy)]
enum Expect {
    At(usize),             // same selection + context, now at this offset
    ContextChanged(usize), // same selection text at this offset, context edited
    Destroyed,             // selection edited or deleted
    Duplicated(usize),     // original still here; an identical copy exists elsewhere
    Reflowed(usize),       // same words, whitespace inside the selection changed
}

#[derive(Default, Serialize, Clone)]
pub struct Tally {
    pub n: usize,
    pub correct: usize,
    pub false_confident: usize,
    pub false_alarm: usize,
    pub outcomes: BTreeMap<String, usize>,
}

#[derive(Serialize)]
pub struct Report {
    pub cases: usize,
    pub seed: u64,
    pub by_mutation: BTreeMap<String, Tally>,
    pub total: Tally,
    pub latency: Stats,
    pub false_confident_examples: Vec<String>,
}

struct Doc {
    text: String,
    blocks: Vec<(usize, usize)>, // top-level block ranges
}

fn word_spans(text: &str, s: usize, e: usize) -> Vec<(usize, usize)> {
    let mut out = Vec::new();
    let mut start = None;
    for (i, c) in text[s..e].char_indices() {
        match (c.is_alphanumeric() || c == '\'' || c == '-', start) {
            (true, None) => start = Some(s + i),
            (false, Some(st)) => {
                out.push((st, s + i));
                start = None;
            }
            _ => {}
        }
    }
    if let Some(st) = start {
        out.push((st, e));
    }
    out
}

fn flip_case(w: &str) -> String {
    if w.chars().any(|c| c.is_lowercase()) { w.to_uppercase() } else { w.to_lowercase() }
}

/// (mutation name, new text, expectation) — or None if not applicable to this case.
fn mutate(kind: &str, d: &Doc, bi: usize, s: usize, e: usize, filler: &str, rng: &mut Rng) -> Option<(String, Expect)> {
    let t = &d.text;
    let (bs, be) = d.blocks[bi];
    let next = d.blocks.get(bi + 1).map(|b| b.0);
    let ins = format!("{filler}\n\n");
    let boundaries: Vec<usize> = d.blocks.iter().map(|b| b.0).collect();
    Some(match kind {
        "insert_before" => {
            let b = *rng.pick(&boundaries.iter().copied().filter(|&b| b <= bs).collect::<Vec<_>>());
            (format!("{}{ins}{}", &t[..b], &t[b..]), Expect::At(s + ins.len()))
        }
        "insert_after" => {
            let after: Vec<usize> = boundaries.iter().copied().filter(|&b| b > be).collect();
            let b = if after.is_empty() { t.len() } else { *rng.pick(&after) };
            let ins = if b == t.len() { format!("\n\n{filler}\n") } else { ins };
            (format!("{}{ins}{}", &t[..b], &t[b..]), Expect::At(s))
        }
        "delete_other" => {
            let others: Vec<usize> = (0..d.blocks.len().saturating_sub(1)).filter(|&i| i != bi).collect();
            if others.is_empty() {
                return None;
            }
            let oi = *rng.pick(&others);
            let (os, oe) = (d.blocks[oi].0, d.blocks[oi + 1].0);
            let ns = if oe <= s { s - (oe - os) } else { s };
            (format!("{}{}", &t[..os], &t[oe..]), Expect::At(ns))
        }
        "move_block" => {
            let nb = next?;
            let cut = &t[bs..nb];
            let rest = format!("{}{}", &t[..bs], &t[nb..]);
            let targets: Vec<usize> = boundaries.iter().copied().filter(|&b| b != bs && b != nb).map(|b| if b > bs { b - cut.len() } else { b }).collect();
            if targets.is_empty() {
                return None;
            }
            let at = *rng.pick(&targets);
            (format!("{}{cut}{}", &rest[..at], &rest[at..]), Expect::At(at + (s - bs)))
        }
        "duplicate_block" => {
            let nb = next?;
            let copy = &t[bs..nb];
            let targets: Vec<usize> = boundaries.iter().copied().filter(|&b| b != bs).collect();
            if targets.is_empty() {
                return None;
            }
            let at = *rng.pick(&targets);
            let orig = if at <= bs { s + copy.len() } else { s };
            (format!("{}{copy}{}", &t[..at], &t[at..]), Expect::Duplicated(orig))
        }
        "edit_inside" => {
            let ws = word_spans(t, s, e);
            let &(ws_, we) = rng.pick(&ws);
            (format!("{}{}{}", &t[..ws_], flip_case(&t[ws_..we]) + "x", &t[we..]), Expect::Destroyed)
        }
        "edit_context" => {
            let q = quote(t, s, e);
            let ctx: Vec<(usize, usize)> = word_spans(t, s - q.prefix.len(), s)
                .into_iter()
                .chain(word_spans(t, e, e + q.suffix.len()))
                .filter(|&(a, b)| t[a..b].is_ascii() && flip_case(&t[a..b]) != t[a..b])
                .collect();
            if ctx.is_empty() {
                return None;
            }
            let &(a, b) = rng.pick(&ctx);
            (format!("{}{}{}", &t[..a], flip_case(&t[a..b]), &t[b..]), Expect::ContextChanged(s))
        }
        "delete_block" => {
            let nb = next?;
            (format!("{}{}", &t[..bs], &t[nb..]), Expect::Destroyed)
        }
        "reflow_inside" => {
            let spaces: Vec<usize> = t[s..e].match_indices(' ').map(|(i, _)| s + i).collect();
            if spaces.is_empty() {
                return None;
            }
            let p = *rng.pick(&spaces);
            (format!("{}\n{}", &t[..p], &t[p + 1..]), Expect::Reflowed(s))
        }
        "compound" => {
            // insert above, delete something below, insert below
            let (t1, _) = mutate("insert_after", d, bi, s, e, filler, rng)?;
            let d1 = Doc { blocks: blocks(&t1).iter().map(|b| (b.start, b.end)).collect(), text: t1 };
            let bi1 = d1.blocks.iter().position(|b| b.0 <= s && e <= b.1)?;
            let (t2, x) = mutate("delete_other", &d1, bi1, s, e, filler, rng)?;
            let Expect::At(s2) = x else { return None };
            let d2 = Doc { blocks: blocks(&t2).iter().map(|b| (b.start, b.end)).collect(), text: t2 };
            let bi2 = d2.blocks.iter().position(|b| b.0 <= s2 && s2 + (e - s) <= b.1)?;
            let (t3, x) = mutate("insert_before", &d2, bi2, s2, s2 + (e - s), filler, rng)?;
            (t3, x)
        }
        _ => unreachable!(),
    })
}

/// Old text gets a pre-existing identical copy of the selection's block; the
/// new text deletes the highlighted original. The survivor is a different passage.
fn delete_dup_original(d: &Doc, bi: usize, s: usize, rng: &mut Rng) -> Option<(String, usize, String)> {
    let (bs, _) = d.blocks[bi];
    let nb = d.blocks.get(bi + 1)?.0;
    let copy = &d.text[bs..nb];
    let targets: Vec<usize> = d.blocks.iter().map(|b| b.0).filter(|&b| b > nb).collect();
    if targets.is_empty() {
        return None;
    }
    let at = *rng.pick(&targets);
    let old = format!("{}{copy}{}", &d.text[..at], &d.text[at..]); // copy after the original: s unchanged
    let new = format!("{}{}", &old[..bs], &old[nb..]);
    Some((old, s, new))
}

const KINDS: &[&str] = &["insert_before", "insert_after", "delete_other", "move_block", "duplicate_block", "edit_inside", "edit_context", "delete_block", "reflow_inside", "compound", "delete_dup_original"];

pub fn run(corpus: &Path, cases: usize, seed: u64) -> anyhow::Result<Report> {
    let docs: Vec<Doc> = md_files(corpus)
        .iter()
        .filter_map(|p| std::fs::read_to_string(p).ok())
        .map(|text| Doc { blocks: blocks(&text).iter().map(|b| (b.start, b.end)).collect(), text })
        .filter(|d| d.blocks.len() > 4)
        .collect();
    anyhow::ensure!(!docs.is_empty(), "no Markdown corpus at {}", corpus.display());
    let mut rng = Rng::new(seed);
    let mut by: BTreeMap<String, Tally> = BTreeMap::new();
    let (mut lat, mut bad) = (Vec::new(), Vec::new());
    let mut made = 0;
    while made < cases {
        let d = rng.pick(&docs);
        let prose: Vec<usize> = (0..d.blocks.len())
            .filter(|&i| {
                let b = &d.text[d.blocks[i].0..d.blocks[i].1];
                b.len() > 120 && b.starts_with(|c: char| c.is_alphabetic())
            })
            .collect();
        if prose.is_empty() {
            continue;
        }
        let bi = *rng.pick(&prose);
        let ws = word_spans(&d.text, d.blocks[bi].0, d.blocks[bi].1);
        if ws.len() < 8 {
            continue;
        }
        let n = 3 + rng.below(10);
        let i = rng.below(ws.len() - n.min(ws.len() - 1));
        let (s, e) = (ws[i].0, ws[(i + n - 1).min(ws.len() - 1)].1);
        let other = rng.pick(&docs);
        let fb = other.blocks[rng.below(other.blocks.len())];
        let filler = other.text[fb.0..fb.1].trim().to_string();
        if filler.is_empty() || filler.contains(&d.text[s..e]) {
            continue;
        }
        let kind = KINDS[made % KINDS.len()];
        let (old, new, expect) = if kind == "delete_dup_original" {
            let Some((old, s2, new)) = delete_dup_original(d, bi, s, &mut rng) else { continue };
            debug_assert_eq!(s2, s);
            (old, new, Expect::Destroyed)
        } else {
            let Some((new, expect)) = mutate(kind, d, bi, s, e, &filler, &mut rng) else { continue };
            (d.text.clone(), new, expect)
        };
        made += 1;

        let q = quote(&old, s, e);
        let (r, dt) = time(|| resolve(&old, &new, &q));
        lat.push(dt);
        let got = r.range.map(|x| x.0);
        let conf = r.resolution.confident();
        let (ok, false_conf, alarm) = match expect {
            Expect::At(p) | Expect::Reflowed(p) => (conf && got == Some(p), conf && got != Some(p), !conf),
            Expect::ContextChanged(p) => (!conf && (r.resolution != Resolution::ContextChanged || got == Some(p)), conf, false),
            Expect::Destroyed => (!conf, conf, false),
            Expect::Duplicated(p) => (!conf || got == Some(p), conf && got != Some(p), false),
        };
        let t = by.entry(kind.into()).or_default();
        t.n += 1;
        t.correct += ok as usize;
        t.false_confident += false_conf as usize;
        t.false_alarm += alarm as usize;
        *t.outcomes.entry(r.resolution.as_str().into()).or_default() += 1;
        if false_conf && bad.len() < 10 {
            bad.push(format!("{kind}: {:?} expected {expect:?} got {:?} at {got:?}", &d.text[s..e], r.resolution));
        }
    }
    let mut total = Tally::default();
    for t in by.values() {
        total.n += t.n;
        total.correct += t.correct;
        total.false_confident += t.false_confident;
        total.false_alarm += t.false_alarm;
        for (k, v) in &t.outcomes {
            *total.outcomes.entry(k.clone()).or_default() += v;
        }
    }
    Ok(Report { cases, seed, by_mutation: by, total, latency: stats(lat), false_confident_examples: bad })
}
