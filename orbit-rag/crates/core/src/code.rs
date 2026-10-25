//! Code structure via tree-sitter: one block per symbol (fn, struct, method, class…)
//! with its enclosing containers (impl, class, mod) as the heading path. Leading
//! comments/attributes stay attached to their symbol; whatever lies between symbols
//! (imports, statements) becomes a gap block, so every non-blank byte is covered.
use crate::sources::Block;
use tree_sitter::{Language, Node, Parser};

pub fn language(path: &str) -> Option<(Language, &'static str)> {
    let ext = path.rsplit('.').next()?;
    Some(match ext {
        "rs" => (tree_sitter_rust::LANGUAGE.into(), "rust"),
        "ts" => (tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(), "typescript"),
        "tsx" => (tree_sitter_typescript::LANGUAGE_TSX.into(), "tsx"),
        "js" | "jsx" | "mjs" | "cjs" => (tree_sitter_javascript::LANGUAGE.into(), "javascript"),
        "py" => (tree_sitter_python::LANGUAGE.into(), "python"),
        _ => return None,
    })
}

const CONTAINERS: &[&str] = &["impl_item", "trait_item", "mod_item", "class_declaration", "abstract_class_declaration", "class", "class_definition", "internal_module"];
const SYMBOLS: &[&str] = &[
    "function_item", "function_signature_item", "struct_item", "enum_item", "union_item", "type_item", "const_item", "static_item", "macro_definition",
    "function_declaration", "generator_function_declaration", "method_definition", "method_signature", "abstract_method_signature",
    "interface_declaration", "type_alias_declaration", "enum_declaration", "lexical_declaration", "variable_declaration", "public_field_definition",
    "function_definition",
];
const TRANSPARENT: &[&str] = &["export_statement", "decorated_definition"];

fn label(n: Node, src: &str) -> String {
    let text = |n: Node| src[n.byte_range()].to_string();
    let field = |f: &str| n.child_by_field_name(f).map(text);
    let prefix = match n.kind() {
        "function_item" | "function_signature_item" => "fn",
        "struct_item" => "struct",
        "enum_item" | "enum_declaration" => "enum",
        "trait_item" => "trait",
        "mod_item" => "mod",
        "impl_item" => "impl",
        "function_declaration" | "generator_function_declaration" => "function",
        "method_definition" | "method_signature" | "abstract_method_signature" => "method",
        "class_declaration" | "abstract_class_declaration" | "class" | "class_definition" => "class",
        "function_definition" => "def",
        "interface_declaration" => "interface",
        "type_alias_declaration" | "type_item" => "type",
        "macro_definition" => "macro",
        _ => "",
    };
    let name = match n.kind() {
        "impl_item" => match (field("trait"), field("type")) {
            (Some(t), Some(ty)) => format!("{t} for {ty}"),
            (_, ty) => ty.unwrap_or_default(),
        },
        "lexical_declaration" | "variable_declaration" => {
            let mut c = n.walk();
            let names: Vec<String> = n.named_children(&mut c).filter_map(|d| d.child_by_field_name("name").map(text)).collect();
            names.join(", ")
        }
        _ => field("name").unwrap_or_default(),
    };
    format!("{prefix} {name}").trim().to_string()
}

struct Walk<'a> {
    src: &'a str,
    symbols: Vec<Block>,
    containers: Vec<(usize, usize, Vec<String>)>,
}

impl Walk<'_> {
    fn visit(&mut self, node: Node, path: &mut Vec<String>, start_override: Option<usize>) {
        let mut cur = node.walk();
        let mut pending: Option<usize> = start_override;
        for child in node.named_children(&mut cur) {
            let k = child.kind();
            if k.contains("comment") || k == "attribute_item" || k == "decorator" {
                pending.get_or_insert(child.start_byte());
                continue;
            }
            let start = pending.take().unwrap_or(child.start_byte());
            if TRANSPARENT.contains(&k) {
                self.visit(child, path, Some(start));
            } else if CONTAINERS.contains(&k) {
                path.push(label(child, self.src));
                self.containers.push((start, child.end_byte(), path.clone()));
                self.visit(child, path, None);
                path.pop();
            } else if SYMBOLS.contains(&k) {
                let mut hp = path.clone();
                hp.push(label(child, self.src));
                self.symbols.push(Block { heading_path: hp, start, end: child.end_byte(), heading: false });
            } else if child.named_child_count() > 0 && !path.is_empty() {
                self.visit(child, path, None); // container bodies: declaration_list, class_body, block
            }
        }
    }
}

pub fn blocks(text: &str, lang: &Language) -> Vec<Block> {
    let mut p = Parser::new();
    let Some(tree) = p.set_language(lang).ok().and_then(|_| p.parse(text, None)) else { return vec![] };
    let mut w = Walk { src: text, symbols: vec![], containers: vec![] };
    w.visit(tree.root_node(), &mut vec![], None);
    let mut syms = w.symbols;
    syms.sort_by_key(|b| b.start);

    // gap-fill: anything between symbols that contains real tokens
    let mut out = Vec::new();
    let mut at = 0;
    let starts: Vec<usize> = w.containers.iter().map(|c| c.0).collect();
    let push_gap = |s: usize, e: usize, out: &mut Vec<Block>| {
        let mut cuts: Vec<usize> = starts.iter().copied().filter(|&c| c > s && c < e).collect();
        cuts.sort_unstable();
        cuts.push(e);
        let mut a = s;
        for b in cuts {
            if text[a..b].chars().any(char::is_alphanumeric) {
                let c = w.containers.iter().filter(|c| c.0 <= a && b <= c.1).max_by_key(|c| c.2.len());
                let (ts, te) = (a + text[a..b].len() - text[a..b].trim_start().len(), a + text[a..b].trim_end().len());
                // a gap that opens its container is that container's header line(s)
                let header = c.is_some_and(|c| c.0 == a);
                out.push(Block { heading_path: c.map(|c| c.2.clone()).unwrap_or_default(), start: ts, end: te, heading: header });
            }
            a = b;
        }
    };
    for b in syms {
        if b.start < at {
            continue; // nested duplicate (shouldn't happen)
        }
        push_gap(at, b.start, &mut out);
        at = b.end;
        out.push(b);
    }
    push_gap(at, text.len(), &mut out);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rust_symbols_carry_their_impl_and_comments() {
        let src = "use std::io;\n\n/// A store.\n#[derive(Debug)]\npub struct Store { a: u8 }\n\nimpl Store {\n    /// Open it.\n    pub fn open() -> Self { todo!() }\n    fn close(&self) {}\n}\n";
        let (lang, _) = language("x.rs").unwrap();
        let b = blocks(src, &lang);
        let paths: Vec<String> = b.iter().map(|b| b.heading_path.join(" > ")).collect();
        assert_eq!(paths, ["", "struct Store", "impl Store", "impl Store > fn open", "impl Store > fn close"], "{b:?}");
        assert!(src[b[1].start..b[1].end].starts_with("/// A store.\n#[derive(Debug)]"));
        assert!(src[b[3].start..b[3].end].starts_with("/// Open it."));
    }

    #[test]
    fn ts_and_python_classes() {
        let ts = "import x from 'y'\nexport class Nav {\n  push(route: string) { return 1 }\n}\nexport const go = () => 1\n";
        let (l, _) = language("a.ts").unwrap();
        let p: Vec<String> = blocks(ts, &l).iter().map(|b| b.heading_path.join(" > ")).collect();
        assert_eq!(p, ["", "class Nav", "class Nav > method push", "go"]);
        let py = "import os\n\nclass A:\n    x = 1\n\n    @staticmethod\n    def f():\n        pass\n";
        let (l, _) = language("a.py").unwrap();
        let b = blocks(py, &l);
        let p: Vec<String> = b.iter().map(|b| b.heading_path.join(" > ")).collect();
        assert_eq!(p, ["", "class A", "class A > def f"], "{b:?}");
        assert!(py[b[2].start..b[2].end].starts_with("@staticmethod"), "{:?}", &py[b[2].start..b[2].end]);
    }
}
