//! Context engine. Modules map 1:1 to the plan's crates; split into separate
//! crates when compile times or ownership boundaries call for it.
#[cfg(feature = "ann")]
pub mod ann;
pub mod anchors;
pub mod bundles;
pub mod code;
pub mod diffs;
pub mod embed;
pub mod model;
pub mod retrieval;
pub mod review;
pub mod sources;
pub mod storage;
pub mod tags;

pub use storage::Store;
