pub fn double(x: i64) -> i64 {
    x * 2
}

#[test]
fn doubles() {
    assert_eq!(double(2), 4);
}
