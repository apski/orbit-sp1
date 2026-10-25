//! Tally: a tiny lot matcher. Doc comments are not refs: NotARef.
pub mod lots;
mod model;

pub use lots::{Fifo, Matcher};
pub use model::Method as Rule;

/* Block comments are skipped:
   BlockComment stays out of refs.
*/
pub fn run(m: &dyn Matcher) -> Method {
    m.method()
}
