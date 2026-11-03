"""Methods shared by arrays and slices: `len`, `get`, `copy_from_slice`, ..

An array carries its length in its type and a slice carries it in the fat
pointer, so both are lowered once, against a pointer and a length.
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


class TestLenAndGet(unittest.TestCase):

    def test_len_and_is_empty(self):
        self.assertEqual(_rs(_body(
            "let a = [1u32, 2, 3, 4];",
            "bad += check(1, a.len() as i64, 4);",
            "bad += check(2, if a.is_empty() { 1 } else { 0 }, 0);",
            "let s: &[u32] = &a[1..3];",
            "bad += check(4, s.len() as i64, 2);",
            "let none: &[u32] = &a[2..2];",
            "bad += check(5, if none.is_empty() { 1 } else { 0 }, 1);")), 0)

    def test_get_returns_none_out_of_range(self):
        self.assertEqual(_rs(_body(
            "let a = [10u32, 20, 30];",
            "bad += check(1, *a.get(1).unwrap() as i64, 20);",
            "bad += check(2, if a.get(3).is_none() { 1 } else { 0 }, 1);",
            "bad += check(3, a.get(2).copied().unwrap_or(0) as i64, 30);",
            "bad += check(4, a.get(9).copied().unwrap_or(77) as i64, 77);",
            "let s: &[u32] = &a[1..];",
            "bad += check(5, *s.get(1).unwrap() as i64, 30);",
            "bad += check(6, if s.get(2).is_none() { 1 } else { 0 }, 1);")), 0)

    def test_get_mut_writes_through(self):
        self.assertEqual(_rs(_body(
            "let mut a = [1u32, 2, 3];",
            "if let Some(slot) = a.get_mut(1) { *slot = 9; }",
            "if let Some(slot) = a.get_mut(5) { *slot = 99; }",
            "bad += check(1, a[1] as i64, 9);",
            "bad += check(2, a[0] as i64 + a[2] as i64, 4);")), 0)

    def test_first_and_last(self):
        self.assertEqual(_rs(_body(
            "let a = [5u32, 6, 7];",
            "bad += check(1, *a.first().unwrap() as i64, 5);",
            "bad += check(2, *a.last().unwrap() as i64, 7);",
            "let none: &[u32] = &a[1..1];",
            "bad += check(3, if none.first().is_none() && none.last().is_none() { 1 } else { 0 }, 1);")), 0)

    def test_index_expression_is_evaluated_once(self):
        self.assertEqual(_rs("""
static mut CALLS: i32 = 0;
fn idx() -> usize { unsafe { CALLS += 1; } 1 }
fn main() -> i32 {
    let a = [4u32, 5, 6];
    let v = *a.get(idx()).unwrap();
    v as i32 * 10 + unsafe { CALLS }
}
"""), 51)

    def test_static_and_struct_field_arrays(self):
        self.assertEqual(_rs("""
static TABLE: [u32; 3] = [7, 8, 9];
struct S { slots: [bool; 4] }
fn main() -> i32 {
    let s = S { slots: [false, true, false, false] };
    let mut r = 0;
    if TABLE.get(2).copied().unwrap_or(0) == 9 { r += 1; }
    if TABLE.len() == 3 { r += 2; }
    if s.slots.get(1).copied().unwrap_or(false) { r += 4; }
    if s.slots.len() == 4 { r += 8; }
    r
}
"""), 15)


class TestBulkOperations(unittest.TestCase):

    def test_copy_from_slice(self):
        self.assertEqual(_rs(_body(
            "let src = [1u8, 2, 3, 4, 5, 6, 7, 8, 9, 10];",
            "let mut w = [0u8; 4];",
            "w.copy_from_slice(&src[3..7]);",
            "bad += check(1, w[0] as i64 * 1000 + w[1] as i64 * 100 + w[2] as i64 * 10 + w[3] as i64, 4567);",
            "let mut dst = [0u8; 10];",
            "dst[2..5].copy_from_slice(&src[0..3]);",
            "bad += check(2, dst[1] as i64 + dst[2] as i64 * 10 + dst[4] as i64 * 100 + dst[5] as i64, 0 + 10 + 300 + 0);")), 0)

    def test_copy_from_slice_of_a_different_length_aborts(self):
        # Rust panics; so does this -- the process dies on SIGABRT instead of
        # overrunning the destination.
        rc = _rs(_body(
            "let src = [1u8, 2, 3, 4];",
            "let mut w = [0u8; 3];",
            "w.copy_from_slice(&src[0..4]);"))
        self.assertNotEqual(rc, 0)
        self.assertIn(rc, (134, -6))

    def test_fill_contains_swap(self):
        self.assertEqual(_rs(_body(
            "let mut a = [0u32; 5];",
            "a.fill(7);",
            "bad += check(1, a[0] as i64 + a[4] as i64, 14);",
            "bad += check(2, if a.contains(&7) { 1 } else { 0 }, 1);",
            "bad += check(3, if a.contains(&8) { 1 } else { 0 }, 0);",
            "let mut b = [1u32, 2, 3];",
            "b.swap(0, 2);",
            "bad += check(4, b[0] as i64 * 100 + b[1] as i64 * 10 + b[2] as i64, 321);")), 0)


class TestRawPointerAccess(unittest.TestCase):

    def test_cast_read_write_and_unaligned(self):
        self.assertEqual(_rs("""
struct Pair { a: u32, b: u32 }
fn main() -> i32 {
    let mut buf = [0u8; 16];
    let base: *mut u8 = &mut buf[0];
    unsafe {
        base.cast::<u32>().write(0x01020304);
        base.add(4).cast::<u32>().write_unaligned(0x11);
        base.add(1).cast::<u8>().write(9);
    }
    let w = unsafe { base.cast::<u32>().read() };
    let v = unsafe { base.add(4).cast::<u32>().read_unaligned() };
    let p: *mut Pair = base.add(8).cast::<Pair>();
    unsafe { p.write(Pair { a: 5, b: 6 }); }
    let q = unsafe { p.read() };
    (buf[1] as i32) * 1000 + (v as i32) + (q.a + q.b) as i32 + if w != 0 { 0 } else { 99999 }
}
"""), (9 * 1000 + 0x11 + 11) & 0xFF)

    def test_cast_takes_its_type_from_the_target_without_a_turbofish(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let mut x: u32 = 0x0A0B0C0D;
    let p: *mut u32 = &mut x;
    let b: *mut u8 = p.cast();
    unsafe { *b as i32 }
}
"""), 0x0D)

    def test_read_on_a_pointee_crust_does_not_know_is_still_an_error(self):
        with self.assertRaises(crust.CrustError):
            crust.translate("fn f(file: &mut File) -> u32 { file.read(1) }")

    def test_wrong_arity_is_named(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate("fn f(p: *mut u32) -> u32 { unsafe { p.read(1) } }")
        self.assertIn("takes 0 arguments", str(cm.exception))


class TestNotSupported(unittest.TestCase):

    def test_unknown_array_method_is_still_named(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate("fn f(a: [u32; 3]) -> u32 { a.frobnicate() }")
        self.assertIn("frobnicate", str(cm.exception))

    def test_a_user_method_named_get_still_wins(self):
        self.assertEqual(_rs("""
struct Bag { n: u32 }
impl Bag { fn get(&self, i: usize) -> u32 { self.n + i as u32 } }
fn main() -> i32 { let b = Bag { n: 40 }; b.get(2) as i32 }
"""), 42)


if __name__ == "__main__":
    unittest.main()
