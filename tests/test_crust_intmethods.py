"""Integer, bool and raw-pointer methods from Rust's core.

Wrapping and saturating arithmetic, `checked_*` (an `Option`), `min`/`max`/
`clamp`, `pow`, bit counting and rotation, `div_ceil`, `is_null`,
`read_volatile` and `bool::then_some`. Signed `wrapping_*` goes through the
unsigned type so C's undefined signed overflow never happens.
"""

import unittest

import shivyc.crust as crust
from tests.test_crust_extras import _rs

_PRE = """
fn check(name: i32, got: i64, want: i64) -> i32 { if got == want { 0 } else { name } }
"""


def _body(*lines):
    return _PRE + "fn main() -> i32 {\n    let mut bad = 0;\n    " + \
        "\n    ".join(lines) + "\n    bad\n}\n"


class TestWrappingAndSaturating(unittest.TestCase):

    def test_wrapping_unsigned(self):
        self.assertEqual(_rs(_body(
            "let m: u32 = 0xFFFFFFFF; let z: u32 = 0; let a: u32 = 7;",
            "bad += check(1, a.wrapping_add(3) as i64, 10);",
            "bad += check(2, m.wrapping_add(2) as i64, 1);",
            "bad += check(3, z.wrapping_sub(1) as i64, 0xFFFFFFFF);",
            "bad += check(4, (1u64 << 40).wrapping_mul(1 << 30) as i64, 0);")), 0)

    def test_wrapping_signed_does_not_trap(self):
        self.assertEqual(_rs(_body(
            "let big: i32 = 2147483647;",
            "bad += check(1, big.wrapping_add(1) as i64, -2147483648);")), 0)

    def test_saturating(self):
        self.assertEqual(_rs(_body(
            "let m: u32 = 0xFFFFFFFF; let a: u32 = 7; let c: u8 = 250;",
            "let big: i32 = 2147483647; let low: i32 = -2147483647 - 1;",
            "bad += check(1, a.saturating_sub(10) as i64, 0);",
            "bad += check(2, m.saturating_add(5) as i64, 0xFFFFFFFF);",
            "bad += check(3, c.saturating_add(10) as i64, 255);",
            "bad += check(4, big.saturating_add(1) as i64, 2147483647);",
            "bad += check(5, low.saturating_sub(1) as i64, -2147483648);")), 0)


class TestChecked(unittest.TestCase):

    def test_checked_results(self):
        self.assertEqual(_rs(_body(
            "let m: u32 = 0xFFFFFFFF; let z: u32 = 0; let a: u32 = 7;",
            "bad += check(1, a.checked_add(3).unwrap() as i64, 10);",
            "bad += check(2, if m.checked_add(1).is_none() { 1 } else { 0 }, 1);",
            "bad += check(3, if z.checked_sub(1).is_none() { 1 } else { 0 }, 1);",
            "bad += check(4, a.checked_sub(3).unwrap_or(99) as i64, 4);",
            "bad += check(5, if m.checked_mul(2).is_none() { 1 } else { 0 }, 1);",
            "bad += check(6, a.checked_mul(3).unwrap() as i64, 21);")), 0)

    def test_signed_checked_add_and_sub(self):
        self.assertEqual(_rs(_body(
            "let big: i32 = 2147483647; let low: i32 = -2147483647 - 1;",
            "bad += check(1, if big.checked_add(1).is_none() { 1 } else { 0 }, 1);",
            "bad += check(2, if low.checked_sub(1).is_none() { 1 } else { 0 }, 1);",
            "bad += check(3, big.checked_sub(1).unwrap() as i64, 2147483646);")), 0)

    def test_signed_checked_mul_is_refused(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate("fn f(a: i32) -> bool { a.checked_mul(2).is_some() }")
        self.assertIn("unsigned", str(cm.exception))


class TestMinMaxPowBits(unittest.TestCase):

    def test_min_max_clamp_abs(self):
        self.assertEqual(_rs(_body(
            "let a: u32 = 7; let s: i32 = -7;",
            "bad += check(1, a.min(3) as i64 * 10 + a.max(3) as i64, 37);",
            "bad += check(2, a.clamp(1, 5) as i64, 5);",
            "bad += check(3, s.abs() as i64, 7);",
            "bad += check(4, a.abs_diff(10) as i64, 3);",
            "bad += check(5, s.signum() as i64, -1);")), 0)

    def test_pow_and_powers_of_two(self):
        self.assertEqual(_rs(_body(
            "let a: u32 = 7;",
            "bad += check(1, a.pow(3) as i64, 343);",
            "bad += check(2, if a.is_power_of_two() { 1 } else { 0 }, 0);",
            "bad += check(3, a.next_power_of_two() as i64, 8);",
            "bad += check(4, if 64u32.is_power_of_two() { 1 } else { 0 }, 1);")), 0)

    def test_bit_counting_and_rotation(self):
        self.assertEqual(_rs(_body(
            "let a: u32 = 7;",
            "bad += check(1, a.count_ones() as i64, 3);",
            "bad += check(2, a.leading_zeros() as i64, 29);",
            "bad += check(3, 8u32.trailing_zeros() as i64, 3);",
            "bad += check(4, 0x80000001u32.rotate_left(1) as i64, 3);",
            "bad += check(5, 9u32.rotate_right(1) as i64, 0x80000004);",
            "bad += check(6, 0u32.count_zeros() as i64, 32);")), 0)

    def test_division_helpers(self):
        self.assertEqual(_rs(_body(
            "let a: u32 = 7; let s: i32 = -7;",
            "bad += check(1, a.div_ceil(3) as i64, 3);",
            "bad += check(2, a.next_multiple_of(4) as i64, 8);",
            "bad += check(3, s.rem_euclid(3) as i64, 2);")), 0)

    def test_operands_are_evaluated_once(self):
        self.assertEqual(_rs("""
static mut CALLS: i32 = 0;
fn next() -> u32 { unsafe { CALLS += 1; } 5 }
fn main() -> i32 {
    let m = next().min(9).max(2).saturating_sub(1);
    m as i32 * 10 + unsafe { CALLS }
}
"""), 41)


class TestPointerAndBool(unittest.TestCase):

    def test_is_null_and_volatile(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let mut x: u32 = 5;
    let p: *mut u32 = &mut x;
    let n: *const u8 = 0 as *const u8;
    unsafe { p.write_volatile(9); }
    let mut r = 0;
    if n.is_null() { r += 1; }
    if !p.is_null() { r += 2; }
    r + unsafe { p.read_volatile() } as i32
}
"""), 12)

    def test_then_some(self):
        self.assertEqual(_rs(_body(
            "let a: u32 = 7;",
            "bad += check(1, (a > 5).then_some(11).unwrap_or(0) as i64, 11);",
            "bad += check(2, (a > 9).then_some(11).unwrap_or(0) as i64, 0);")), 0)

    def test_then_runs_its_closure_only_when_true(self):
        self.assertEqual(_rs("""
static mut CALLS: i32 = 0;
fn bump() -> i32 { unsafe { CALLS += 1; } 20 }
fn main() -> i32 {
    let a: u32 = 7;
    let yes = (a > 5).then(|| bump() + 1);
    let no = (a > 9).then(|| bump() + 100);
    let mut r = 0;
    if yes.is_some() { r += yes.unwrap(); }
    if no.is_none() { r += 100; }
    if a.is_multiple_of(7) { r += 10; }
    if !a.is_multiple_of(2) { r += 1; }
    r + unsafe { CALLS } * 1000 / 1000 - 100
}
"""), 33)

    def test_a_user_method_of_the_same_name_still_wins(self):
        self.assertEqual(_rs("""
struct W(u32);
impl W { fn min(&self, o: u32) -> u32 { o + 100 } }
fn main() -> i32 { let w = W(5); (w.min(3) - 100) as i32 }
"""), 3)


class TestCratePaths(unittest.TestCase):

    def test_crate_and_super_prefixes_in_expressions(self):
        self.assertEqual(_rs("""
const BASE: i32 = 40;
fn two() -> i32 { 2 }
fn main() -> i32 { crate::BASE + crate::two() + self::two() - 2 }
"""), 42)


class TestRestrictedVisibilityGenerics(unittest.TestCase):

    def test_pub_crate_generic_struct_stays_a_template(self):
        # `pub(crate)` used to hide the generics from the template scan, so
        # `T` was resolved eagerly and the struct failed to compile.
        self.assertEqual(_rs("""
pub(crate) struct Slot<T> { value: UnsafeCell<MaybeUninit<T>>, set: bool }
impl<T> Slot<T> {
    pub(crate) const fn empty() -> Self {
        Slot { value: UnsafeCell::new(MaybeUninit::uninit()), set: false }
    }
    pub(crate) fn mark(&mut self) { self.set = true; }
}
pub(in crate::x) struct Pair<A, B> { a: A, b: B }
fn main() -> i32 {
    let mut s: Slot<i32> = Slot::empty();
    s.mark();
    let p: Pair<i32, i32> = Pair { a: 3, b: 4 };
    if s.set { p.a + p.b } else { 0 }
}
"""), 7)


    def test_pub_crate_associated_const(self):
        self.assertEqual(_rs("""
struct Rank(u16);
impl Rank {
    pub(crate) const LOW: u16 = 3;
    pub(crate) const HIGH: u16 = 9;
    pub(crate) fn span() -> u16 { Rank::HIGH - Rank::LOW }
}
fn main() -> i32 { Rank::span() as i32 }
"""), 6)


class TestPrimitiveConversions(unittest.TestCase):

    def test_from_is_typed_and_lossless(self):
        self.assertEqual(_rs(_body(
            "let a: u32 = 0xFFFFFFFF; let n: i32 = -3; let b: u8 = 200;",
            "let w = u64::from(a);",
            "bad += check(1, (w + 1) as i64, 0x100000000);",
            "bad += check(2, i64::from(n), -3);",
            "let sig = u64::from(a as u32);",
            "bad += check(3, (sig >> 31) as i64, 1);",
            "bad += check(4, u32::from(b) as i64 * 2, 400);",
            "let f = f64::from(n);",
            "bad += check(5, if f < -2.5 && f > -3.5 { 1 } else { 0 }, 1);")), 0)

    def test_try_from_accepts_exactly_the_values_that_fit(self):
        self.assertEqual(_rs(_body(
            "let big: u64 = 0xFFFFFFFFFFFFFFFF; let neg: i64 = -1;",
            "bad += check(1, if u8::try_from(255u32).is_ok() { 1 } else { 0 }, 1);",
            "bad += check(2, if u8::try_from(256u32).is_err() { 1 } else { 0 }, 1);",
            "bad += check(3, if u8::try_from(-1i32).is_err() { 1 } else { 0 }, 1);",
            "bad += check(4, if u64::try_from(neg).is_err() { 1 } else { 0 }, 1);",
            "bad += check(5, if i32::try_from(big).is_err() { 1 } else { 0 }, 1);",
            "bad += check(6, if i64::try_from(big).is_err() { 1 } else { 0 }, 1);",
            "bad += check(7, if i8::try_from(-128i32).is_ok() { 1 } else { 0 }, 1);",
            "bad += check(8, if i8::try_from(128i32).is_err() { 1 } else { 0 }, 1);",
            "bad += check(9, if u32::try_from(neg).is_err() { 1 } else { 0 }, 1);",
            "bad += check(10, if u64::try_from(5i64).is_ok() { 1 } else { 0 }, 1);",
            "bad += check(11, if usize::try_from(big).is_ok() { 1 } else { 0 }, 1);",
            "bad += check(12, if i64::try_from(5u64).is_ok() { 1 } else { 0 }, 1);",
            "bad += check(13, u16::try_from(65535u64).unwrap() as i64, 65535);",
            "bad += check(14, u16::try_from(65536u64).unwrap_or(7) as i64, 7);")), 0)

    def test_try_from_with_ok_question_mark_and_match(self):
        self.assertEqual(_rs("""
fn lookup(op: u64) -> Option<usize> {
    let v = usize::try_from(op).ok()?;
    Some(v + 1)
}
fn narrow(x: i64) -> i32 {
    match i32::try_from(x) { Ok(v) => v, Err(_) => -1 }
}
fn main() -> i32 {
    let mut r = 0;
    if lookup(4).unwrap() == 5 { r += 1; }
    if narrow(9) == 9 { r += 2; }
    if narrow(0x100000000) == -1 { r += 4; }
    r
}
"""), 7)

    def test_a_user_from_impl_still_wins(self):
        self.assertEqual(_rs("""
struct Meters(u32);
impl From<u32> for Meters { fn from(v: u32) -> Meters { Meters(v * 100) } }
fn main() -> i32 { let m = Meters::from(3); m.0 as i32 }
"""), 44)       # 300 & 0xFF


class TestAssignmentTarget(unittest.TestCase):
    """The place on the left of `=` names the type of the value on the right."""

    def test_none_into_an_indexed_field(self):
        self.assertEqual(_rs("""
struct Entry { object: u64, generation: u32 }
struct Table { entries: [Option<Entry>; 4] }
impl Table {
    fn put(&mut self, slot: usize) {
        self.entries[slot] = Some(Entry { object: 7, generation: 1 });
    }
    fn clear(&mut self, slot: usize) { self.entries[slot] = None; }
    fn live(&self, slot: usize) -> bool { self.entries[slot].is_some() }
}
fn main() -> i32 {
    let mut t = Table { entries: [None, None, None, None] };
    t.put(2);
    let mut r = 0;
    if t.live(2) { r += 1; }
    t.clear(2);
    if !t.live(2) { r += 2; }
    r
}
"""), 3)

    def test_none_into_a_plain_field_and_a_local(self):
        self.assertEqual(_rs("""
struct S { o: Option<u32> }
fn main() -> i32 {
    let mut s = S { o: Some(4) };
    let mut local: Option<u32> = Some(9);
    s.o = None;
    local = None;
    if s.o.is_none() && local.is_none() { 5 } else { 0 }
}
"""), 5)

    def test_empty_vec_assigned_through_a_reference(self):
        self.assertEqual(_rs("""
fn reset(v: &mut Vec<u32>) { *v = Vec::new(); }
fn main() -> i32 {
    let mut v: Vec<u32> = Vec::new();
    v.push(1); v.push(2);
    reset(&mut v);
    v.len() as i32 + 6
}
"""), 6)


class TestItemForms(unittest.TestCase):

    def test_generic_tuple_structs(self):
        self.assertEqual(_rs("""
struct Inner<T> { v: T, n: u32 }
pub(crate) struct Wrap<T>(Inner<T>);
impl<T> Wrap<T> {
    pub(crate) fn new(v: T) -> Self { Self(Inner { v: v, n: 1 }) }
    pub(crate) fn count(&self) -> u32 { self.0.n }
    pub(crate) fn bump(&mut self) { self.0.n += 1; }
}
struct Pair<A, B>(A, B);
fn main() -> i32 {
    let mut w: Wrap<i32> = Wrap::new(5);
    w.bump();
    w.bump();
    let p: Pair<i32, i32> = Pair(3, 4);
    (w.count() as i32) * 10 + p.0 + p.1 - 7
}
"""), 30)

    def test_item_after_a_generic_tuple_struct_is_not_swallowed(self):
        # The span scan used to run on to the next `{` and take the impl.
        self.assertEqual(_rs("""
struct W<T>(T);
impl<T> W<T> { fn get(&self) -> T { self.0 } }
fn main() -> i32 { let w: W<i32> = W(9); w.get() }
"""), 9)

    def test_empty_statements_and_semicolon_after_a_block(self):
        self.assertEqual(_rs("""
fn f(x: u32) -> u32 {
    unsafe { let _y = x + 1; };
    ;
    { let _z = x; };
    x + 2
}
fn main() -> i32 { f(3) as i32 }
"""), 5)


if __name__ == "__main__":
    unittest.main()
