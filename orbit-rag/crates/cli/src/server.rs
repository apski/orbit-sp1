//! Loopback-only HTTP API + static viewer. Single process, single request thread;
//! a second thread drains the embedding outbox so captures never wait on a model.
use anyhow::{bail, Result};
use orbit_core::bundles::{bundle_with, render_markdown};
use orbit_core::embed::Embedder;
use orbit_core::retrieval::{search_with, SearchQuery};
use orbit_core::storage::{byte_to_utf16, utf16_to_byte};
use orbit_core::Store;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use tiny_http::{Header, Method, Request, Response, Server};

const DIST: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../apps/viewer/dist");

pub fn serve(store: Store, port: u16, open_path: Option<String>, open_browser: bool) -> Result<()> {
    let addr = format!("127.0.0.1:{port}");
    let server = Server::http(&addr).map_err(|e| anyhow::anyhow!("bind {addr}: {e}"))?;
    let dist = std::env::var("ORBIT_VIEWER_DIST").map(PathBuf::from).unwrap_or_else(|_| PathBuf::from(DIST));
    let dist = dist.canonicalize().map_err(|_| anyhow::anyhow!("viewer not built: run `npm run build` in apps/viewer ({})", dist.display()))?;

    let root = store.root.clone();
    std::thread::spawn(move || {
        let (Ok(s), Ok(Some(e))) = (Store::discover(&root), Embedder::from_env()) else { return };
        loop {
            let _ = s.index_pending(&e); // retried every tick while the provider is down
            std::thread::sleep(std::time::Duration::from_secs(1));
        }
    });

    let url = format!("http://{addr}/{}", open_path.map(|p| format!("?path={}", enc(&p))).unwrap_or_default());
    eprintln!("orbit-rag viewer on {url}");
    if open_browser {
        let _ = std::process::Command::new("open").arg(&url).spawn();
    }
    let embedder = Embedder::from_env()?;
    let allowed_hosts = [format!("127.0.0.1:{port}"), format!("localhost:{port}")];

    for mut req in server.incoming_requests() {
        let host_ok = header(&req, "Host").is_some_and(|h| allowed_hosts.contains(&h)); // DNS-rebinding guard
        let resp = if !host_ok {
            Err((403, "bad host".to_string()))
        } else {
            route(&store, embedder.as_ref(), &dist, &mut req)
        };
        let _ = match resp {
            Ok(r) => req.respond(r),
            Err((code, msg)) => req.respond(json_resp(code, &json!({ "error": msg }))),
        };
    }
    Ok(())
}

type Resp = Response<std::io::Cursor<Vec<u8>>>;

fn header(req: &Request, name: &'static str) -> Option<String> {
    req.headers().iter().find(|h| h.field.equiv(name)).map(|h| h.value.to_string())
}

fn json_resp(code: u16, v: &Value) -> Resp {
    Response::from_string(v.to_string())
        .with_status_code(code)
        .with_header(Header::from_bytes("Content-Type", "application/json").unwrap())
}

fn enc(s: &str) -> String {
    s.bytes().map(|b| if b.is_ascii_alphanumeric() || b"-_./~".contains(&b) { (b as char).to_string() } else { format!("%{b:02X}") }).collect()
}

fn dec(s: &str) -> String {
    let b = s.as_bytes();
    let hex = |i: usize| b.get(i..i + 2).and_then(|h| std::str::from_utf8(h).ok()).and_then(|h| u8::from_str_radix(h, 16).ok());
    let (mut out, mut i) = (Vec::with_capacity(b.len()), 0);
    while i < b.len() {
        match (b[i], hex(i + 1)) {
            (b'+', _) => out.push(b' '),
            (b'%', Some(v)) => {
                out.push(v);
                i += 2;
            }
            (c, _) => out.push(c),
        }
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn route(s: &Store, e: Option<&Embedder>, dist: &Path, req: &mut Request) -> Result<Resp, (u16, String)> {
    let url = req.url().to_string();
    let (path, qs) = url.split_once('?').unwrap_or((&url, ""));
    let q: HashMap<String, String> = qs.split('&').filter_map(|kv| kv.split_once('=')).map(|(k, v)| (dec(k), dec(v))).collect();
    let bad = |e: anyhow::Error| (400u16, format!("{e:#}"));
    let body = if *req.method() == Method::Post {
        // JSON-only POSTs: cross-origin pages can't send these without a CORS preflight we never answer
        if !header(req, "Content-Type").is_some_and(|c| c.starts_with("application/json")) {
            return Err((415, "application/json required".into()));
        }
        let mut b = String::new();
        std::io::Read::read_to_string(req.as_reader(), &mut b).map_err(|e| (400, e.to_string()))?;
        serde_json::from_str::<Value>(&b).map_err(|e| (400, e.to_string()))?
    } else {
        Value::Null
    };
    let get = |k: &str| q.get(k).cloned().unwrap_or_default();
    let field = |k: &str| body[k].as_str().map(str::to_string);

    let v = match (req.method(), path) {
        (Method::Get, "/api/files") => {
            let sources: Vec<Value> = s.db.prepare("SELECT id, path FROM sources ORDER BY path").and_then(|mut st| {
                st.query_map([], |r| Ok(json!({ "id": r.get::<_, String>(0)?, "path": r.get::<_, String>(1)? })))?.collect()
            }).map_err(|e| bad(e.into()))?;
            json!({ "files": s.markdown_files().map_err(bad)?, "sources": sources })
        }
        (Method::Get, "/api/doc") => doc(s, &get("path")).map_err(bad)?,
        (Method::Post, "/api/highlights") => {
            let src = field("source_id").unwrap_or_default();
            let rev = body["revision_id"].as_i64().unwrap_or(-1);
            let latest = s.latest_revision(&src).map_err(bad)?.ok_or((404, "unknown source".to_string()))?;
            let text = s.snapshot(&latest.snapshot_hash).map_err(bad)?;
            let off = |k: &str| body[k].as_u64().and_then(|u| utf16_to_byte(&text, u as usize)).ok_or((400u16, format!("bad {k}")));
            let (start, end) = (off("start")?, off("end")?);
            let id = s
                .capture(&src, rev, start, end, &field("category").unwrap_or("note".into()), field("note").filter(|n| !n.is_empty()).as_deref(), field("scope").filter(|n| !n.is_empty()).as_deref(), false)
                .map_err(|e| (409, format!("{e:#}")))?;
            json!({ "id": id })
        }
        (Method::Get, "/api/search") => {
            let sq = SearchQuery { text: get("q"), scope: q.get("scope").filter(|x| !x.is_empty()).cloned(), limit: 12, include_code: get("code") == "1", ..Default::default() };
            let eng = crate::engine(s, e).map_err(bad)?;
            let res = search_with(s, &eng.retrievers(s, e), &sq).map_err(bad)?;
            serde_json::to_value(res).unwrap()
        }
        (Method::Get, "/api/review") => serde_json::to_value(s.review_list().map_err(bad)?).unwrap(),
        (Method::Post, "/api/review/accept") => {
            s.review_accept(&field("id").unwrap_or_default()).map_err(bad)?;
            json!({ "ok": true })
        }
        (Method::Post, "/api/review/retire") => {
            s.retire(&field("id").unwrap_or_default()).map_err(bad)?;
            json!({ "ok": true })
        }
        (Method::Get, "/api/bundle") => {
            let tokens = get("tokens").parse().unwrap_or(4000);
            let scope = q.get("scope").filter(|x| !x.is_empty()).cloned();
            let eng = crate::engine(s, e).map_err(bad)?;
            let sq = SearchQuery { text: get("task"), scope, include_code: get("code") == "1", ..Default::default() };
            let b = bundle_with(s, &eng.retrievers(s, e), &sq, tokens).map_err(bad)?;
            json!({ "markdown": render_markdown(&b), "bundle": b })
        }
        (Method::Get, p) if !p.starts_with("/api/") => return static_file(dist, p),
        _ => return Err((404, "not found".into())),
    };
    Ok(json_resp(200, &v))
}

fn doc(s: &Store, rel: &str) -> Result<Value> {
    if rel.is_empty() {
        bail!("path required");
    }
    let src = s.open_source(&s.root.join(rel))?; // validates + syncs (re-anchors on change)
    let rev = s.latest_revision(&src)?.unwrap();
    let content = s.snapshot(&rev.snapshot_hash)?;
    let highlights: Vec<Value> = s
        .records_where("source_id = ?1 AND status NOT IN ('retired','superseded')", [&src])?
        .into_iter()
        .map(|r| {
            let range = r.start.zip(r.end).filter(|_| r.source_revision_id == rev.id).map(|(a, b)| (byte_to_utf16(&content, a), byte_to_utf16(&content, b)));
            json!({ "id": r.id, "category": r.category, "validity": r.validity(), "resolution": r.resolution, "authority": r.authority,
                    "note": r.note, "body": r.body, "scope": r.scope, "start": range.map(|x| x.0), "end": range.map(|x| x.1) })
        })
        .collect();
    let lang = orbit_core::sources::fence_lang(&rev.path);
    let mut st = s.db.prepare(
        "SELECT t.id, t.kind, t.name, t.value, t.start_offset, t.end_offset FROM tags t JOIN chunks c ON c.id = t.chunk_id WHERE c.revision_id = ?1 ORDER BY t.start_offset",
    )?;
    let tags: Vec<Value> = st
        .query_map([rev.id], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?, r.get::<_, String>(2)?, r.get::<_, Option<String>>(3)?, r.get::<_, usize>(4)?, r.get::<_, usize>(5)?)))?
        .map(|t| t.map(|(id, kind, name, value, a, b)| json!({ "id": format!("tag_{id}"), "kind": kind, "name": name, "value": value, "start": byte_to_utf16(&content, a), "end": byte_to_utf16(&content, b) })))
        .collect::<rusqlite::Result<_>>()?;
    Ok(json!({ "source_id": src, "path": rev.path, "lang": lang, "revision_id": rev.id, "git_rev": rev.git_rev, "content": content, "highlights": highlights, "tags": tags }))
}

fn static_file(dist: &Path, p: &str) -> Result<Resp, (u16, String)> {
    let rel = if p == "/" { "index.html" } else { p.trim_start_matches('/') };
    let file = dist.join(rel).canonicalize().ok().filter(|f| f.starts_with(dist) && f.is_file()).or_else(|| Some(dist.join("index.html"))).unwrap();
    let bytes = std::fs::read(&file).map_err(|e| (404, e.to_string()))?;
    let ct = match file.extension().and_then(|e| e.to_str()) {
        Some("html") => "text/html; charset=utf-8",
        Some("js") => "text/javascript",
        Some("css") => "text/css",
        Some("svg") => "image/svg+xml",
        _ => "application/octet-stream",
    };
    Ok(Response::from_data(bytes)
        .with_header(Header::from_bytes("Content-Type", ct).unwrap())
        .with_header(Header::from_bytes("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:").unwrap()))
}
