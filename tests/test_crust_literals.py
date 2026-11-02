"""Literal and initializer forms real crates lean on.

Byte strings and byte chars, struct field shorthand and `..base`, `const { }`
blocks, and array repeats whose length is a const expression or whose value is
a struct, including in `static` initializers where C allows only constants.
"""

import unittest

import shivyc.crust as crust
from tests.test_crust_extras import _rs


class TestByteLiterals(unittest.TestCase):

    def test_byte_string_as_a_fixed_array_reference(self):
        self.assertEqual(_rs("""
const MAGIC: &[u8; 8] = b"GOCICAT2";
fn main() -> i32 { MAGIC[0] as i32 + MAGIC[7] as i32 }
"""), 71 + 50)

    def test_byte_string_as_a_slice_has_its_length(self):
        self.assertEqual(_rs("""
static ID: &[u8] = b"HELLO";
fn main() -> i32 { ID.len() as i32 * 10 + ID[1] as i32 - 69 }
"""), 50)

    def test_byte_string_passed_where_a_slice_is_expected(self):
        self.assertEqual(_rs("""
fn sum(b: &[u8]) -> i32 { let mut s = 0; for x in b { s += *x as i32; } s }
fn main() -> i32 { let a: &[u8] = b"AB"; sum(a) }
"""), 131)

    def test_escapes_count_as_one_byte_each(self):
        self.assertEqual(_rs("""
fn main() -> i32 { let z: &[u8] = b"a\\0b\\n\\x41"; z.len() as i32 * 100 + z[4] as i32 - 65 }
"""), (500) & 0xFF)

    def test_byte_char(self):
        self.assertEqual(_rs("""
fn main() -> i32 { (b'A' as i32) + (b'\\n' as i32) }
"""), 75)

    def test_unterminated_byte_string_is_reported(self):
        with self.assertRaises(crust.CrustError):
            crust.translate('fn f() { let a = b"abc; }')


class TestStructLiteralForms(unittest.TestCase):

    def test_field_init_shorthand(self):
        self.assertEqual(_rs("""
struct Cmd { command: u32, base: u32 }
fn build(command: u32, register_base: u32) -> Cmd {
    Cmd {
        command,
        base: register_base,
    }
}
fn main() -> i32 { let c = build(3, 4); (c.command * 10 + c.base) as i32 }
"""), 34)

    def test_shorthand_with_every_field(self):
        self.assertEqual(_rs("""
struct P { a: i32, b: i32 }
fn main() -> i32 { let a = 4; let b = 5; let p = P { a, b }; p.a * 10 + p.b }
"""), 45)

    def test_functional_update(self):
        self.assertEqual(_rs("""
struct P { a: i32, b: i32, c: i32 }
fn main() -> i32 {
    let base = P { a: 1, b: 2, c: 3 };
    let q = P { b: 20, ..base };
    q.a + q.b + q.c
}
"""), 24)

    def test_unknown_field_is_still_reported(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate("""
struct P { a: i32 }
fn f(z: i32) -> P { P { z } }
""")
        self.assertIn("no field `z`", str(cm.exception))


class TestRepeatsAndConstBlocks(unittest.TestCase):

    def test_repeat_length_is_a_const_expression(self):
        self.assertEqual(_rs("""
const N: usize = 4;
const M: usize = N * 2;
static F: [u32; M] = [7; M];
fn main() -> i32 { (F[7] + M as u32) as i32 }
"""), 15)

    def test_repeat_length_with_a_cast(self):
        self.assertEqual(_rs("""
const N: u32 = 3;
static F: [u32; 3] = [2; N as usize];
fn main() -> i32 { (F[0] + F[2]) as i32 }
"""), 4)

    def test_static_array_of_structs_repeated(self):
        self.assertEqual(_rs("""
const N: usize = 4;
const M: usize = N * 2;
struct Page { e: [u32; 4] }
static T: [Page; M] = [Page { e: [0; 4] }; M];
fn main() -> i32 { (T[1].e[2] + T[7].e[3]) as i32 }
"""), 0)

    def test_const_block_repeating_an_atomic(self):
        self.assertEqual(_rs("""
static A: [AtomicU64; 4] = [const { AtomicU64::new(0) }; 4];
fn main() -> i32 {
    A[2].fetch_add(5, Ordering::SeqCst);
    A[2].load(Ordering::SeqCst) as i32 + A[1].load(Ordering::SeqCst) as i32
}
"""), 5)

    def test_suffixed_zero_repeat_needs_no_annotation(self):
        self.assertEqual(_rs("""
const SECTOR_SIZE: usize = 512;
fn main() -> i32 {
    let mut h = [0_u8; SECTOR_SIZE];
    h[3] = 9;
    h[3] as i32 + h[500] as i32
}
"""), 9)

    def test_non_constant_length_is_still_refused(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate("fn f(n: usize) -> i32 { let a = [1; n]; 0 }")
        self.assertIn("repeat length", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
