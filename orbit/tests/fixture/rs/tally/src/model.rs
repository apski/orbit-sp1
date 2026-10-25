#[derive(Clone, Copy, Debug)]
pub enum Method {
    Fifo,
    Hifo,
}

pub(crate) type LotId = u64;

pub struct Lot<'a, T: Into<f64>> {
    pub id: LotId,
    pub qty: T,
    pub note: &'a str,
}

union Bits {
    f: f64,
    u: u64,
}
