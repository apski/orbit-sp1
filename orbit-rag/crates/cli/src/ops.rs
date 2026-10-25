//! Operations: watch, backup/restore, integrity check.
use anyhow::{bail, Context, Result};
use orbit_core::embed::Embedder;
use orbit_core::sources::sha256;
use orbit_core::Store;
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

fn stamp() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs())
}

// ponytail: polls every `interval` (hash compare per source); swap in FSEvents/inotify
// via the notify crate if repos get large enough for polling to show up in profiles.
pub fn watch(interval: f64) -> Result<()> {
    let store = Store::discover(&std::env::current_dir()?)?;
    let emb = Embedder::from_env()?;
    eprintln!("watching {} (ctrl-c to stop)", store.root.display());
    loop {
        let rep = store.sync_all()?;
        for (id, res) in &rep.reanchored {
            println!("re-anchored {id}: {res}");
        }
        for id in &rep.flagged {
            println!("needs review: {id}");
        }
        if let Some(e) = &emb {
            match store.index_pending(e) {
                Ok(n) if n > 0 => println!("embedded {n}"),
                Err(err) => eprintln!("embedding deferred: {err:#}"),
                _ => {}
            }
        }
        std::thread::sleep(Duration::from_secs_f64(interval));
    }
}

/// `VACUUM INTO` gives a consistent snapshot without stopping writers; then zstd.
pub fn backup(store: &Store) -> Result<()> {
    let dir = store.dir().join("backups");
    std::fs::create_dir_all(&dir)?;
    let raw = dir.join(format!("orbit-{}.db", stamp()));
    store.db.execute("VACUUM INTO ?1", [raw.to_string_lossy()])?;
    let out = raw.with_extension("db.zst");
    let mut enc = zstd::Encoder::new(std::fs::File::create(&out)?, 19)?;
    enc.long_distance_matching(true)?;
    std::io::copy(&mut std::fs::File::open(&raw)?, &mut enc)?;
    enc.finish()?;
    let (a, b) = (std::fs::metadata(&raw)?.len(), std::fs::metadata(&out)?.len());
    std::fs::remove_file(&raw)?;
    println!("{} ({} → {} bytes, {:.1}x)", out.display(), a, b, a as f64 / b.max(1) as f64);
    Ok(())
}

pub fn restore(cwd: &Path, file: &Path) -> Result<()> {
    let store = Store::discover(cwd)?;
    let orbit = store.dir();
    drop(store);
    let tmp = orbit.join("orbit.db.restore");
    let mut dec = zstd::Decoder::new(std::fs::File::open(file).with_context(|| file.display().to_string())?)?;
    std::io::copy(&mut dec, &mut std::fs::File::create(&tmp)?)?;
    let ok: String = rusqlite::Connection::open(&tmp)?.query_row("PRAGMA integrity_check", [], |r| r.get(0))?;
    if ok != "ok" {
        std::fs::remove_file(&tmp)?;
        bail!("backup failed integrity check: {ok}");
    }
    let keep = orbit.join(format!("orbit.db.bak-{}", stamp()));
    std::fs::rename(orbit.join("orbit.db"), &keep)?;
    for suffix in ["-wal", "-shm"] {
        let _ = std::fs::rename(orbit.join(format!("orbit.db{suffix}")), format!("{}{suffix}", keep.display()));
    }
    std::fs::rename(&tmp, orbit.join("orbit.db"))?;
    let _ = std::fs::remove_dir_all(orbit.join("lance")); // derived; rebuilt on next use
    println!("restored {}; previous store kept at {}", file.display(), keep.display());
    Ok(())
}

pub fn check(s: &Store) -> Result<()> {
    let mut problems = Vec::new();
    let one = |sql: &str| -> Result<i64> { Ok(s.db.query_row(sql, [], |r| r.get(0))?) };
    let ic: String = s.db.query_row("PRAGMA integrity_check", [], |r| r.get(0))?;
    if ic != "ok" {
        problems.push(format!("integrity_check: {ic}"));
    }
    let fk = one("SELECT COUNT(*) FROM pragma_foreign_key_check")?;
    if fk > 0 {
        problems.push(format!("{fk} foreign key violations"));
    }
    let mut st = s.db.prepare("SELECT hash, content FROM snapshots")?;
    for row in st.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))? {
        let (h, c) = row?;
        if sha256(&c) != h {
            problems.push(format!("snapshot {h} content does not match its hash"));
        }
    }
    let orphans = one("SELECT COUNT(*) FROM records r WHERE NOT EXISTS (SELECT 1 FROM anchors WHERE record_id = r.id) OR NOT EXISTS (SELECT 1 FROM record_revisions WHERE record_id = r.id)")?;
    if orphans > 0 {
        problems.push(format!("{orphans} records without an anchor or revision"));
    }
    let (chunks, fts) = (one("SELECT COUNT(*) FROM chunks")?, one("SELECT COUNT(*) FROM chunks_fts")?);
    if chunks != fts {
        problems.push(format!("chunks ({chunks}) and chunks_fts ({fts}) differ — run `orbit-rag reindex`"));
    }
    let (recs, rfts) = (one("SELECT COUNT(*) FROM records")?, one("SELECT COUNT(*) FROM records_fts")?);
    if recs != rfts {
        problems.push(format!("records ({recs}) and records_fts ({rfts}) differ — run `orbit-rag reindex`"));
    }
    let pending = one("SELECT COUNT(*) FROM embed_outbox")?;
    let open = one("SELECT COUNT(*) FROM review_items WHERE state = 'open'")?;
    println!("sources {} · records {recs} · chunks {chunks} · open reviews {open} · embeddings pending {pending}", one("SELECT COUNT(*) FROM sources")?);
    if problems.is_empty() {
        println!("ok");
        Ok(())
    } else {
        problems.iter().for_each(|p| println!("problem: {p}"));
        bail!("{} problem(s)", problems.len())
    }
}

/// Code constants vs docs. Exit status 1 when a current doc disagrees.
pub fn drift(s: &Store, json: bool) -> Result<()> {
    let d = orbit_core::model::drift(s)?;
    if json {
        println!("{}", serde_json::to_string_pretty(&d)?);
    } else {
        for x in &d {
            println!("{:<7} {} = {} ({})\n        {}: {}\n", x.verdict, x.constant, x.value, x.code, x.doc, x.sentence);
        }
    }
    let n = d.iter().filter(|x| x.verdict == "drift").count();
    eprintln!("{n} drift, {} history", d.len() - n);
    if n > 0 {
        std::process::exit(1);
    }
    Ok(())
}
