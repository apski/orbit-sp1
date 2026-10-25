use std::fmt;
use crate::model::{Lot, Method};

pub trait Matcher {
    fn method(&self) -> Method;
}

pub struct Fifo;

impl Matcher for Fifo {
    fn method(&self) -> Method {
        Method::Fifo
    }
}

impl<'a, T: Into<f64>> fmt::Display for Lot<'a, T> {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "{}", self.id)
    }
}

impl Fifo {
    pub fn new() -> Self {
        Fifo
    }
}
