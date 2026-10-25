use std::time::{Duration, Instant};

/// xorshift64*: deterministic, dependency-free RNG for fixture generation.
pub struct Rng(u64);

impl Rng {
    pub fn new(seed: u64) -> Rng {
        Rng(seed.wrapping_mul(0x9E3779B97F4A7C15) | 1)
    }
    pub fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545F4914F6CDD1D)
    }
    pub fn below(&mut self, n: usize) -> usize {
        (self.next() % n.max(1) as u64) as usize
    }
    pub fn pick<'a, T>(&mut self, v: &'a [T]) -> &'a T {
        &v[self.below(v.len())]
    }
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct Stats {
    pub n: usize,
    pub p50_ms: f64,
    pub p95_ms: f64,
    pub max_ms: f64,
}

pub fn stats(mut d: Vec<Duration>) -> Stats {
    d.sort();
    let ms = |x: Duration| x.as_secs_f64() * 1e3;
    let at = |p: f64| d.get(((d.len() as f64 * p).ceil() as usize).saturating_sub(1)).copied().map_or(0.0, ms);
    Stats { n: d.len(), p50_ms: at(0.5), p95_ms: at(0.95), max_ms: d.last().copied().map_or(0.0, ms) }
}

pub fn time<T>(f: impl FnOnce() -> T) -> (T, Duration) {
    let t = Instant::now();
    let r = f();
    (r, t.elapsed())
}

/// Peak resident set size of this process, in MB (macOS reports bytes, Linux KB).
pub fn peak_rss_mb() -> f64 {
    let mut u: libc::rusage = unsafe { std::mem::zeroed() };
    unsafe { libc::getrusage(libc::RUSAGE_SELF, &mut u) };
    let v = u.ru_maxrss as f64;
    if cfg!(target_os = "macos") { v / 1048576.0 } else { v / 1024.0 }
}

pub fn md_files(dir: &std::path::Path) -> Vec<std::path::PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![dir.to_path_buf()];
    while let Some(d) = stack.pop() {
        for e in std::fs::read_dir(&d).into_iter().flatten().flatten() {
            let p = e.path();
            let name = e.file_name().to_string_lossy().to_string();
            if name.starts_with('.') {
                continue;
            }
            if p.is_dir() {
                stack.push(p);
            } else if name.ends_with(".md") {
                out.push(p);
            }
        }
    }
    out.sort();
    out
}
