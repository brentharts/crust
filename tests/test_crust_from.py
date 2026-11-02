"""`From` / `Into`, and `?` converting an error through `From`.

`impl From<S> for T` already lowered to a plain `T_from`. These cover what was
missing: several `From` impls on one type (each needs its own C name and is
chosen by the argument's type), `x.into()` (the target comes from the type the
context expects), and `?` on a `Result` whose error type differs from the
function's.
"""

import unittest

import shivyc.crust as crust
from tests.test_crust_extras import _rs

_UNITS = """
struct Meters(i32);
struct Feet(i32);
struct Inches(i32);

impl From<Feet> for Meters {
    fn from(f: Feet) -> Meters { Meters(f.0 / 3) }
}
impl From<Inches> for Meters {
    fn from(i: Inches) -> Self { Meters(i.0 / 40) }
}
"""


class TestFromOverloads(unittest.TestCase):

    def test_single_impl_keeps_its_plain_name(self):
        c = crust.translate("""
struct A(i32);
struct B(i32);
impl From<A> for B { fn from(a: A) -> B { B(a.0) } }
fn main() -> i32 { B::from(A(1)).0 }
""")
        self.assertIn("B_from(", c)
        self.assertNotIn("B_from__", c)

    def test_several_impls_are_chosen_by_argument_type(self):
        self.assertEqual(_rs(_UNITS + """
fn main() -> i32 {
    let a = Meters::from(Feet(30));
    let b = Meters::from(Inches(400));
    a.0 * 100 + b.0
}
"""), (10 * 100 + 10) & 0xFF)

    def test_second_impl_gets_a_distinct_c_name(self):
        c = crust.translate(_UNITS + "fn main() -> i32 { 0 }\n")
        self.assertIn("Meters_from(", c)
        self.assertIn("Meters_from__", c)

    def test_wrong_source_type_is_refused(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate(_UNITS + """
fn main() -> i32 { Meters::from(7).0 }
""")
        self.assertIn("no `From<", str(cm.exception))
        self.assertIn("Feet", str(cm.exception))


class TestInto(unittest.TestCase):

    def test_into_from_a_let_annotation(self):
        self.assertEqual(_rs(_UNITS + """
fn main() -> i32 {
    let c: Meters = Feet(9).into();
    let d: Meters = Inches(80).into();
    c.0 * 10 + d.0
}
"""), 32)

    def test_into_from_a_parameter_type(self):
        self.assertEqual(_rs(_UNITS + """
fn takes(m: Meters) -> i32 { m.0 }
fn main() -> i32 { takes(Feet(6).into()) + takes(Inches(120).into()) }
"""), 5)

    def test_into_from_a_return_type(self):
        self.assertEqual(_rs(_UNITS + """
fn conv(f: Feet) -> Meters { f.into() }
fn main() -> i32 { conv(Feet(12)).0 }
"""), 4)

    def test_into_in_a_struct_field(self):
        self.assertEqual(_rs(_UNITS + """
struct Track { len: Meters, id: i32 }
fn main() -> i32 {
    let t = Track { len: Feet(15).into(), id: 3 };
    t.len.0 * 10 + t.id
}
"""), 53)

    def test_numeric_widening(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let a: i32 = 40;
    let w: i64 = a.into();
    (w + 2) as i32
}
"""), 42)

    def test_into_the_same_type_is_the_identity(self):
        self.assertEqual(_rs("""
struct P(i32);
fn main() -> i32 { let p: P = P(9).into(); p.0 }
"""), 9)

    def test_an_explicit_into_impl_still_wins(self):
        self.assertEqual(_rs("""
struct A(i32);
struct B(i32);
impl Into<B> for A { fn into(self) -> B { B(self.0 + 1) } }
fn main() -> i32 { let b: B = A(4).into(); b.0 }
"""), 5)

    def test_into_without_a_target_is_refused(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate(_UNITS + """
fn main() -> i32 { let x = Feet(3).into(); 0 }
""")
        self.assertIn("what `.into()` converts to", str(cm.exception))

    def test_into_with_no_matching_impl_is_refused(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate(_UNITS + """
struct Other(i32);
fn main() -> i32 { let m: Meters = Other(1).into(); m.0 }
""")
        self.assertIn("no `From<Other>` for `Meters`", str(cm.exception))


_ERRORS = """
enum ParseError { Empty, Bad(i32) }
enum AppError { Parse(ParseError), Other }
impl From<ParseError> for AppError {
    fn from(e: ParseError) -> AppError { AppError::Parse(e) }
}
fn parse(n: i32) -> Result<i32, ParseError> {
    if n == 0 { Err(ParseError::Empty) }
    else if n < 0 { Err(ParseError::Bad(n)) }
    else { Ok(n) }
}
"""


class TestQuestionMarkConversion(unittest.TestCase):

    def test_error_is_converted_through_from(self):
        self.assertEqual(_rs(_ERRORS + """
fn run(n: i32) -> Result<i32, AppError> {
    let v = parse(n)?;
    Ok(v * 2)
}
fn main() -> i32 {
    let mut r = 0;
    match run(5) { Ok(v) => r += v, Err(_) => r += 100 }
    match run(0) {
        Err(AppError::Parse(ParseError::Empty)) => r += 1,
        _ => r += 100,
    }
    match run(-4) {
        Err(AppError::Parse(ParseError::Bad(x))) => r += x + 10,
        _ => r += 100,
    }
    r
}
"""), 10 + 1 + 6)

    def test_matching_error_types_still_pass_through(self):
        self.assertEqual(_rs(_ERRORS + """
fn run(n: i32) -> Result<i32, ParseError> {
    let v = parse(n)?;
    Ok(v + 1)
}
fn main() -> i32 { match run(4) { Ok(v) => v, Err(_) => 100 } }
"""), 5)

    def test_missing_from_impl_is_refused_and_says_what_to_add(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate("""
enum A { X }
enum B { Y }
fn f() -> Result<i32, A> { Err(A::X) }
fn g() -> Result<i32, B> { let v = f()?; Ok(v) }
""")
        self.assertIn("impl From<A> for B", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
