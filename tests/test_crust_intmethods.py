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


if __name__ == "__main__":
    unittest.main()
