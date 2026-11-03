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


class TestByteConversions(unittest.TestCase):

    def test_from_bytes_forms(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let a = u32::from_le_bytes([1, 2, 3, 4]);
    let b = u32::from_be_bytes([1, 2, 3, 4]);
    let arr: [u8; 2] = [0x34, 0x12];
    let c = u16::from_le_bytes(arr);
    if a != 0x04030201 { return 1; }
    if b != 0x01020304 { return 2; }
    if c != 0x1234 { return 3; }
    0
}
"""), 0)

    def test_slice_try_into_and_amp_array_argument(self):
        self.assertEqual(_rs("""
fn rd(bytes: &[u8], off: usize) -> Option<u32> {
    let w: [u8; 4] = bytes[off..off + 4].try_into().ok()?;
    Some(u32::from_le_bytes(w))
}
fn main() -> i32 {
    let data = [1u8, 0, 0, 0, 2, 0, 0, 0];
    let a = rd(&data, 0).unwrap();
    let b = u32::from_le_bytes(data[4..8].try_into().unwrap());
    if a != 1 || b != 2 { return 1; }
    0
}
"""), 0)

    def test_to_bytes_binds_with_let(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let b = 0x0102u16.to_be_bytes();
    let l = 0x01020304u32.to_le_bytes();
    if b[0] != 1 || b[1] != 2 { return 1; }
    if l[0] != 4 || l[3] != 1 { return 2; }
    0
}
"""), 0)

    def test_length_mismatch_aborts(self):
        st = _rs("""
fn main() -> i32 {
    let v = [1u8, 2, 3];
    let s: &[u8] = &v;
    u32::from_le_bytes(s.try_into().unwrap()) as i32
}
""")
        self.assertNotEqual(st, 0)


class TestGetRangeAndArrayValues(unittest.TestCase):

    def test_get_with_a_range(self):
        self.assertEqual(_rs("""
fn rd(b: &[u8]) -> Option<u32> {
    let w: [u8; 4] = b.get(2..6)?.try_into().ok()?;
    Some(u32::from_le_bytes(w))
}
fn main() -> i32 {
    let d = [0u8, 0, 1, 0, 0, 0, 9];
    let s: &[u8] = &d;
    if rd(s).unwrap() != 1 { return 1; }
    if rd(&d[..5]).is_some() { return 2; }
    if s.get(5..).unwrap().len() != 2 { return 3; }
    if s.get(..=2).unwrap().len() != 3 { return 4; }
    if s.get(4..2).is_some() { return 5; }
    if d.get(1..8).is_some() { return 6; }
    0
}
"""), 0)

    def test_arrays_as_return_param_and_option_payload(self):
        self.assertEqual(_rs("""
struct L { a: u64 }
fn mk(base: u64) -> [L; 2] { [L { a: base }, L { a: base + 1 }] }
fn sum(x: [L; 2]) -> u64 { x[0].a + x[1].a }
fn opt(b: u64) -> Option<[L; 2]> { Some(mk(b)) }
fn take<const N: usize>(b: u64) -> Option<[u64; N]> {
    let mut o = [0u64; N]; let mut i = 0;
    while i < N { o[i] = b + i as u64; i += 1; }
    Some(o)
}
fn main() -> i32 {
    let a = mk(1);
    let [p, q] = opt(10).unwrap();
    let r: [L; 2] = mk(100);
    let n = a.len() + r.len();
    let [z] = take::<1>(7).unwrap();
    let t = take::<3>(1).map(|f| f[2]).unwrap();
    (sum(a) + p.a + q.a + r[1].a + n as u64 + z + t) as i32 - 139
}
"""), 0)

    def test_array_param_in_match(self):
        self.assertEqual(_rs("""
fn f(a: [i32; 3]) -> i32 { match a { [1, _, z] => z, [x, y, z] => x + y + z } }
fn main() -> i32 { f([1, 2, 30]) + f([2, 3, 4]) - 39 }
"""), 0)


class TestOptionUnitAndPointerForms(unittest.TestCase):

    def test_option_of_unit(self):
        self.assertEqual(_rs("""
fn check(x: u32) -> Option<()> { if x > 5 { return None; } Some(()) }
fn chain(x: u32) -> Option<u32> { check(x)?; Some(x + 1) }
fn set_once(slot: &mut Option<u64>, v: u64) -> Option<()> {
    if slot.is_some() { return None; }
    *slot = Some(v);
    Some(())
}
fn main() -> i32 {
    if chain(3).unwrap() != 4 { return 1; }
    if chain(9).is_some() { return 2; }
    let mut s: Option<u64> = None;
    if set_once(&mut s, 5).is_none() { return 4; }
    if set_once(&mut s, 6).is_some() { return 5; }
    check(2).unwrap();
    if check(2).ok_or(7u32).is_err() { return 6; }
    0
}
"""), 0)

    def test_starts_with_ends_with_and_as_ref(self):
        self.assertEqual(_rs("""
struct I { a: u32 }
const H: &[u8] = b"AB";
fn first(p: *const I) -> u32 { let Some(i) = (unsafe { p.as_ref() }) else { return 0 }; i.a }
fn main() -> i32 {
    let d = [65u8, 66, 67];
    let s: &[u8] = &d;
    if !s.starts_with(H) { return 1; }
    if !s.ends_with(&[66u8, 67]) { return 2; }
    if s.starts_with(&[66u8]) { return 3; }
    let i = I { a: 9 };
    if first(&i as *const I) != 9 { return 4; }
    if first(0 as *const I) != 0 { return 5; }
    0
}
"""), 0)

    def test_array_reference_keeps_length_for_copy_from_slice(self):
        self.assertEqual(_rs("""
const M: &[u8; 4] = b"GINX";
fn fill(b: &mut [u8]) -> usize { b[..4].copy_from_slice(M); M.len() }
fn main() -> i32 {
    let mut buf = [0u8; 8];
    let n = fill(&mut buf);
    buf[4..8].copy_from_slice(&0x01020304u32.to_le_bytes());
    if n != 4 || buf[0] != 71 || buf[4] != 4 || buf[7] != 1 { return 1; }
    0
}
"""), 0)


class TestArrayOfOptionsAndMaybeUninit(unittest.TestCase):

    def test_get_on_an_array_of_options(self):
        self.assertEqual(_rs("""
#[derive(Clone, Copy)]
struct E { a: u32 }
struct Table { entries: [Option<E>; 4] }
impl Table {
    fn entry(&self, i: usize) -> Option<E> { self.entries.get(i).copied().flatten() }
}
fn main() -> i32 {
    let mut t = Table { entries: [None; 4] };
    t.entries[2] = Some(E { a: 7 });
    if t.entry(2).unwrap().a != 7 { return 1; }
    if t.entry(1).is_some() || t.entry(9).is_some() { return 2; }
    0
}
"""), 0)

    def test_assume_init_drop(self):
        self.assertEqual(_rs("""
use core::mem::MaybeUninit;
fn main() -> i32 {
    let mut m: MaybeUninit<u32> = MaybeUninit::uninit();
    m.write(3u32);
    unsafe { m.assume_init_drop(); }
    0
}
"""), 0)


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
