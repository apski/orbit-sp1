//! Machine tags for code, derived on every reindex and never authoritative:
//! - subsystem (level 1): from the path, see `subsystem`;
//! - kind (level 2): constant, type, config (env reads), boundary (process,
//!   network, fs, sql), check (guards), api (public symbols), ceiling (TODO,
//!   FIXME, `ponytail:` notes).
//! Tree-sitter for constants and types, plain text scans for the rest.
use tree_sitter::{Node, Parser};

#[derive(Debug, Clone, PartialEq)]
pub struct Tag {
    pub kind: &'static str,
    pub name: String,
    pub value: Option<String>,
    pub start: usize,
    pub end: usize,
}

/// Level-1 subsystem: the path minus layout noise (`crates/`, `src/`, `lib.rs`…),
/// e.g. `crates/core/src/retrieval.rs` -> `core/retrieval`.
// ponytail: path-based; an import-graph clustering would group cross-directory
// subsystems, add it when paths stop matching how a repo is organised.
pub fn subsystem(path: &str) -> String {
    const NOISE: &[&str] = &["crates", "packages", "apps", "src", "lib", "app", "source"];
    const FILES: &[&str] = &["lib", "main", "mod", "index", "__init__"];
    let mut parts: Vec<&str> = path.split('/').filter(|p| !NOISE.contains(p)).collect();
    if let Some(last) = parts.pop() {
        let stem = last.split('.').next().unwrap_or(last);
        if !FILES.contains(&stem) || parts.is_empty() {
            parts.push(stem);
        }
    }
    parts.join("/")
}

fn text(n: Node, src: &str) -> String {
    src[n.byte_range()].to_string()
}

fn is_const_name(s: &str) -> bool {
    s.len() > 1 && s.chars().all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '_') && s.chars().any(|c| c.is_ascii_uppercase())
}

/// Names of a type's direct members (fields, variants, properties, methods).
fn members(n: Node, src: &str) -> Vec<String> {
    let Some(body) = n.child_by_field_name("body").or_else(|| n.child_by_field_name("value")) else { return vec![] };
    let mut c = body.walk();
    body.named_children(&mut c)
        .filter_map(|m| {
            let m = if m.kind() == "expression_statement" { m.named_child(0)? } else { m };
            m.child_by_field_name("name").or_else(|| m.child_by_field_name("left")).map(|x| text(x, src))
        })
        .filter(|s| s.len() <= 60)
        .collect()
}

fn walk(n: Node, src: &str, out: &mut Vec<Tag>) {
    let k = n.kind();
    let span = (n.start_byte(), n.end_byte());
    let short = |v: String| (v.len() <= 120).then_some(v);
    match k {
        // Rust
        "const_item" | "static_item" => {
            if let (Some(name), Some(v)) = (n.child_by_field_name("name"), n.child_by_field_name("value")) {
                out.push(Tag { kind: "constant", name: text(name, src), value: short(text(v, src)), start: span.0, end: span.1 });
            }
        }
        // TS/JS: `const UPPER = literal`
        "lexical_declaration" if src[n.byte_range()].starts_with("const") => {
            let mut c = n.walk();
            for d in n.named_children(&mut c).filter(|d| d.kind() == "variable_declarator") {
                if let (Some(name), Some(v)) = (d.child_by_field_name("name"), d.child_by_field_name("value")) {
                    let name = text(name, src);
                    if is_const_name(&name) && matches!(v.kind(), "number" | "string" | "template_string" | "binary_expression" | "unary_expression" | "true" | "false") {
                        out.push(Tag { kind: "constant", name, value: short(text(v, src)), start: span.0, end: span.1 });
                    }
                }
            }
        }
        // Python: `UPPER = literal` at module or class level
        "assignment" => {
            if let (Some(l), Some(v)) = (n.child_by_field_name("left"), n.child_by_field_name("right")) {
                let name = text(l, src);
                if is_const_name(&name) && matches!(v.kind(), "integer" | "float" | "string" | "binary_operator" | "unary_operator" | "true" | "false") {
                    out.push(Tag { kind: "constant", name, value: short(text(v, src)), start: span.0, end: span.1 });
                }
            }
        }
        "struct_item" | "enum_item" | "union_item" | "trait_item" | "interface_declaration" | "type_alias_declaration" | "enum_declaration" | "class_declaration"
        | "abstract_class_declaration" | "class_definition" => {
            if let Some(name) = n.child_by_field_name("name") {
                let m = members(n, src);
                // mark the declaration line, not the whole body
                let end = src[span.0..span.1].find('\n').map_or(span.1, |i| span.0 + i);
                out.push(Tag { kind: "type", name: text(name, src), value: (!m.is_empty()).then(|| m.join(", ")), start: span.0, end });
            }
        }
        _ => {}
    }
    // function bodies hold locals, not the model
    if !matches!(k, "function_item" | "function_declaration" | "function_definition" | "method_definition" | "arrow_function" | "closure_expression") {
        let mut c = n.walk();
        for ch in n.named_children(&mut c) {
            walk(ch, src, out);
        }
    }
}

/// First identifier or quoted string after `at` (env var names).
fn env_name(s: &str) -> Option<String> {
    let s = s.trim_start_matches(['(', '[', '.', ' ']);
    let s = s.strip_prefix(['"', '\'']).unwrap_or(s);
    let name: String = s.chars().take_while(|c| c.is_ascii_alphanumeric() || *c == '_').collect();
    (!name.is_empty()).then_some(name)
}

const ENV: &[&str] = &["env::var(", "env::var_os(", "process.env", "import.meta.env", "os.environ.get(", "os.environ[", "os.getenv("];
const BOUNDARY: &[(&str, &[&str])] = &[
    ("process", &["Command::new", "child_process", "subprocess.", "execFile(", "Deno.run"]),
    ("network", &["ureq::", "reqwest::", "fetch(", "axios", "requests.", "urllib", "tiny_http", "TcpListener", "XMLHttpRequest", "new WebSocket"]),
    ("fs", &["std::fs::", "fs::read", "fs::write", "File::open", "File::create", "fs.readFile", "fs.writeFile", "pathlib"]),
    ("sql", &[".execute(", ".prepare(", "query_row(", "execute_batch(", "cursor.execute", "INSERT INTO", "SELECT "]),
];
const CHECKS: &[&str] = &["assert!(", "assert_eq!(", "assert_ne!(", "ensure!(", "bail!(", "debug_assert!(", "throw new ", "raise ", "assert "];
const CEILINGS: &[&str] = &["ponytail:", "TODO", "FIXME", "HACK", "XXX"];

fn line_tags(src: &str, out: &mut Vec<Tag>) {
    let mut at = 0;
    for line in src.split_inclusive('\n') {
        let (s, e) = (at, at + line.trim_end().len());
        at += line.len();
        let t = line.trim();
        let is_comment = ["//", "#", "/*", "*", "--"].iter().any(|c| t.starts_with(c));
        if is_comment {
            if let Some(c) = CEILINGS.iter().find(|c| t.contains(*c)) {
                out.push(Tag { kind: "ceiling", name: c.trim_end_matches(':').to_string(), value: Some(t.trim_start_matches(['/', '#', '*', ' ', '-']).chars().take(160).collect()), start: s, end: e });
            }
            continue;
        }
        for p in ENV {
            for (i, _) in line.match_indices(p) {
                if let Some(n) = env_name(&line[i + p.len()..]) {
                    out.push(Tag { kind: "config", name: n, value: None, start: s + i, end: e });
                }
            }
        }
        for (kind, pats) in BOUNDARY {
            if pats.iter().any(|p| line.contains(p)) {
                out.push(Tag { kind: "boundary", name: kind.to_string(), value: Some(t.chars().take(120).collect()), start: s, end: e });
            }
        }
        if CHECKS.iter().any(|c| if c.ends_with("!(") { t.contains(c) } else { t.starts_with(c) || t.contains(&format!(" {c}")) }) {
            out.push(Tag { kind: "check", name: "guard".into(), value: Some(t.chars().take(160).collect()), start: s, end: e });
        }
    }
}

/// Public symbols: `pub` / `export` / top-level Python names without a leading `_`.
fn api_tags(path: &str, src: &str, out: &mut Vec<Tag>) {
    for b in crate::sources::structure(path, src).into_iter().filter(|b| !b.heading && !b.heading_path.is_empty()) {
        let mut at = b.start;
        let mut code = None;
        for l in src[b.start..b.end].split_inclusive('\n') {
            let t = l.trim();
            if !t.is_empty() && !["///", "//", "#[", "#!", "@", "/*", "*", "#"].iter().any(|c| t.starts_with(c)) {
                code = Some((at + (l.len() - l.trim_start().len()), at + l.trim_end().len(), t));
                break;
            }
            at += l.len();
        }
        let Some((ls, le, first)) = code else { continue };
        let public = first.starts_with("pub ") || first.starts_with("pub async") || first.starts_with("export ") || first.starts_with("pub fn");
        let py = path.ends_with(".py") && b.heading_path.len() == 1 && (first.starts_with("def ") || first.starts_with("class ")) && !first[4..].trim_start().starts_with('_');
        if public || py {
            let sig = first.trim_end_matches('{').trim().chars().take(160).collect();
            out.push(Tag { kind: "api", name: b.heading_path.join(" > "), value: Some(sig), start: ls, end: le });
        }
    }
}

/// Test code describes the tests, not the system: test files and `mod tests` are skipped.
fn is_test_file(path: &str) -> bool {
    let p = path.to_lowercase();
    ["/tests/", "/test/", "/__tests__/", ".test.", ".spec.", "_test.", "test_"].iter().any(|t| p.contains(t)) || p.starts_with("tests/")
}

pub fn tags(path: &str, src: &str) -> Vec<Tag> {
    let Some((lang, _)) = crate::code::language(path) else { return vec![] };
    if is_test_file(path) {
        return vec![];
    }
    let mut out = Vec::new();
    let mut tests: Vec<(usize, usize)> = Vec::new();
    let mut p = Parser::new();
    if let Some(tree) = p.set_language(&lang).ok().and_then(|_| p.parse(src, None)) {
        walk(tree.root_node(), src, &mut out);
        let root = tree.root_node();
        let mut c = root.walk();
        tests = root
            .named_children(&mut c)
            .filter(|n| n.kind() == "mod_item" && n.child_by_field_name("name").is_some_and(|x| &src[x.byte_range()] == "tests"))
            .map(|n| (n.start_byte(), n.end_byte()))
            .collect();
    }
    line_tags(src, &mut out);
    api_tags(path, src, &mut out);
    out.retain(|t| !tests.iter().any(|&(s, e)| t.start >= s && t.start < e));
    out.sort_by_key(|t| (t.start, t.kind));
    out.dedup_by(|a, b| a.kind == b.kind && a.name == b.name && a.start == b.start);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn kinds(t: &[Tag]) -> Vec<String> {
        t.iter().map(|t| format!("{}:{}={}", t.kind, t.name, t.value.clone().unwrap_or_default())).collect()
    }

    #[test]
    fn rust_constants_types_config_boundaries() {
        let src = "/// cap\nconst MAX_HUNK_BYTES: usize = 4000;\npub struct Hunk { path: String, text: String }\nimpl Hunk {\n    pub fn git(&self) {\n        // ponytail: shells out per call\n        let model = std::env::var(\"ORBIT_EMBED\");\n        let o = Command::new(\"git\").output();\n        anyhow::ensure!(o.is_ok(), \"git failed\");\n    }\n}\n";
        let k = kinds(&tags("src/diffs.rs", src));
        for want in ["constant:MAX_HUNK_BYTES=4000", "type:Hunk=path, text", "config:ORBIT_EMBED=", "boundary:process=let o = Command::new(\"git\").output();", "ceiling:ponytail=ponytail: shells out per call", "api:struct Hunk=pub struct Hunk { path: String, text: String }"] {
            assert!(k.contains(&want.to_string()), "{want} not in {k:#?}");
        }
        assert!(k.iter().any(|x| x.starts_with("check:guard=anyhow::ensure!")), "{k:#?}");
    }

    #[test]
    fn ts_and_python() {
        let ts = "export const RATE_LIMIT_PER_MINUTE = 90;\nconst local = 3;\nexport interface Spot { id: string; lat: number }\nconst base = process.env.TIDEPOOL_API_URL ?? 'x';\n";
        let k = kinds(&tags("src/config.ts", ts));
        assert!(k.contains(&"constant:RATE_LIMIT_PER_MINUTE=90".into()), "{k:#?}");
        assert!(!k.iter().any(|x| x.contains("local")), "{k:#?}");
        assert!(k.contains(&"type:Spot=id, lat".into()) && k.contains(&"config:TIDEPOOL_API_URL=".into()), "{k:#?}");
        let py = "import os\nTIMEOUT_S = 30\nclass Job:\n    retries = 3\n    def run(self):\n        raise ValueError('x')\n";
        let k = kinds(&tags("jobs.py", py));
        assert!(k.contains(&"constant:TIMEOUT_S=30".into()) && k.contains(&"type:Job=retries, run".into()), "{k:#?}");
        assert!(k.iter().any(|x| x.starts_with("check:guard=raise ValueError")), "{k:#?}");
    }

    #[test]
    fn subsystems() {
        assert_eq!(subsystem("crates/core/src/retrieval.rs"), "core/retrieval");
        assert_eq!(subsystem("crates/core/src/lib.rs"), "core");
        assert_eq!(subsystem("src/api/client.ts"), "api/client");
        assert_eq!(subsystem("main.py"), "main");
    }
}
