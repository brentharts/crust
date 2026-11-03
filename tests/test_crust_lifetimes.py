"""Lifetime-parameterised items and definition-order independence."""

import unittest

import shivyc.crust as crust
from tests.test_crust_extras import _rs


class TestLifetimeStructs(unittest.TestCase):

    def test_struct_impl_and_uses_with_lifetimes(self):
        self.assertEqual(_rs("""
pub(crate) struct Image<'a> { code: &'a [u8], n: usize }
impl<'a> Image<'a> {
    fn len(&self) -> usize { self.code.len() + self.n }
}
fn parse(b: &[u8]) -> Option<Image<'_>> {
    if b.len() == 0 { return None; }
    Some(Image { code: b, n: 1 })
}
fn total(i: Image<'static>) -> usize { i.len() }
fn main() -> i32 {
    let data = [1u8, 2, 3];
    let s: &[u8] = &data;
    let img = parse(s).unwrap();
    img.len() as i32 - 4
}
"""), 0)

    def test_nested_empty_lifetime_list(self):
        crust.translate("struct R<'a> { b: &'a [u8] } "
                        "fn f(x: Option<Option<R<'_>>>) -> u32 { 1 }")


class TestOrder(unittest.TestCase):

    def test_generic_over_struct_defined_later(self):
        self.assertEqual(_rs("""
struct Holder { v: Vec<Item> }
struct Item { n: i32 }
fn main() -> i32 {
    let mut h = Holder { v: Vec::new() };
    h.v.push(Item { n: 7 });
    h.v.len() as i32 - 1
}
"""), 0)

    def test_static_above_struct_it_instantiates(self):
        crust.translate("""
use core::mem::MaybeUninit;
struct Slot { v: MaybeUninit<Manager> }
struct Manager { a: u32 }
static S: Option<Manager> = None;
""")


class TestMoreItemForms(unittest.TestCase):

    def test_lifetime_enum(self):
        crust.translate("pub(crate) enum Op<'a> { A(&'a [u8]), B } "
                        "fn f(o: Op<'_>) -> u32 { match o { Op::A(_) => 1, "
                        "Op::B => 2 } }")

    def test_tuple_struct_field_visibility(self):
        crust.translate("pub(super) struct E(pub(super) [u8; 4]); "
                        "pub(crate) struct L(pub(crate) u32);")

    def test_multiline_const_generic_defaults_are_not_items(self):
        crust.translate("""
struct A<
    const N: usize = 4,
    const M: usize = 8,
> { x: u32 }
struct B { y: u32 }
fn f(b: B) -> u32 { b.y }
""")

    def test_const_generic_function(self):
        self.assertEqual(_rs("""
fn total<const N: usize>(base: u32) -> u32 {
    let mut t = 0; let mut i = 0;
    while i < N { t += base + i as u32; i += 1; }
    t
}
fn size<const N: usize>() -> usize { let a = [0u8; N]; a.len() }
fn main() -> i32 { (total::<3>(10) + size::<5>() as u32) as i32 - 38 }
"""), 0)


class TestCallsIntoOtherFiles(unittest.TestCase):

    def _crate(self, files):
        import os, tempfile
        d = tempfile.mkdtemp()
        for name, text in files.items():
            with open(os.path.join(d, name), "w") as f:
                f.write(text)
        return d

    def test_crate_path_call_takes_the_callee_signature(self):
        import os
        d = self._crate({
            "lib.rs": "mod sync;\nmod user;\n",
            "sync.rs": "pub fn entry_lock() -> u32 { 7 }\n"
                       "pub fn pick<const N: usize>(b: u32) -> u32 { b }\n",
            "user.rs": "use crate::sync;\n"
                       "fn f() -> u32 { let a = crate::sync::entry_lock();"
                       " let b = sync::pick::<2>(1); a + b }\n",
        })
        path = os.path.join(d, "user.rs")
        with open(path) as fh:
            out = crust.translate(fh.read(), path)
        self.assertIn("unsigned int a = sync_entry_lock()", out)
        self.assertIn("sync_pick_2", out)

    def test_call_into_another_workspace_crate(self):
        import os, tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "crates", "svc", "src"))
        os.makedirs(os.path.join(d, "kernel", "src"))
        with open(os.path.join(d, "Cargo.toml"), "w") as f:
            f.write('[workspace]\nmembers = ["crates/svc", "kernel"]\n')
        with open(os.path.join(d, "crates", "svc", "Cargo.toml"), "w") as f:
            f.write('[package]\nname = "my-svc"\n')
        with open(os.path.join(d, "crates", "svc", "src", "lib.rs"), "w") as f:
            f.write("mod inner;\npub use self::inner::recover;\n")
        with open(os.path.join(d, "crates", "svc", "src", "inner.rs"), "w") as f:
            f.write("pub fn recover(x: u32) -> Option<u32> { Some(x) }\n")
        with open(os.path.join(d, "kernel", "Cargo.toml"), "w") as f:
            f.write('[package]\nname = "kern"\n')
        user = os.path.join(d, "kernel", "src", "lib.rs")
        with open(user, "w") as f:
            f.write("use my_svc::recover;\n"
                    "pub fn f() -> bool { recover(3).is_none() }\n")
        with open(user) as fh:
            out = crust.translate(fh.read(), user)
        self.assertIn("recover(3)", out)


class TestBlockFinalJump(unittest.TestCase):

    def test_return_without_semicolon_in_block(self):
        self.assertEqual(_rs("""
fn f(x: Option<u32>) -> u32 { let Some(v) = x else { return 1 }; v + 1 }
fn g(n: u32) -> u32 { if n > 1 { return 10 } 20 }
fn main() -> i32 { (f(None) + f(Some(4)) + g(5) + g(0)) as i32 - 36 }
"""), 0)


if __name__ == "__main__":
    unittest.main()
