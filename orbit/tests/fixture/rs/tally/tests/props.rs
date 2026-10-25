use tally::lots::{Fifo, Matcher};

#[test]
fn fifo_is_fifo() {
    let _ = Fifo::new().method();
}
