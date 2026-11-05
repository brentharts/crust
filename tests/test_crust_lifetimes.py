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

    def test_imported_type_constant_and_sibling_struct_in_a_seeded_file(self):
        import os
        d = self._crate({
            "lib.rs": "mod memory;\nmod user;\n",
            "memory.rs": "use core::mem::MaybeUninit;\n"
                         "pub struct Owner { slot: MaybeUninit<Alloc> }\n"
                         "pub struct Alloc { n: u32 }\n"
                         "pub struct Lease { a: u64 }\n"
                         "impl Lease { pub const EMPTY: Self = Self { a: 0 }; }\n",
            "user.rs": "use crate::memory::{self, Lease};\n"
                       "fn f() -> u64 { let last = Lease::EMPTY;"
                       " let other = memory::Lease::EMPTY; last.a + other.a }\n",
        })
        path = os.path.join(d, "user.rs")
        with open(path) as fh:
            out = crust.translate(fh.read(), path)
        self.assertIn("Lease last = Lease_EMPTY", out)
        self.assertIn("Lease other = Lease_EMPTY", out)


class TestBlockFinalJump(unittest.TestCase):

    def test_return_without_semicolon_in_block(self):
        self.assertEqual(_rs("""
fn f(x: Option<u32>) -> u32 { let Some(v) = x else { return 1 }; v + 1 }
fn g(n: u32) -> u32 { if n > 1 { return 10 } 20 }
fn main() -> i32 { (f(None) + f(Some(4)) + g(5) + g(0)) as i32 - 36 }
"""), 0)


class TestItemOrderAndReceivers(unittest.TestCase):

    def test_by_value_mut_self_and_typed_self(self):
        self.assertEqual(_rs("""
struct C { n: u32 }
impl C {
    fn bump(mut self) -> C { self.n += 1; self }
    fn get(self: &Self) -> u32 { self.n }
}
fn main() -> i32 { let c = C { n: 4 }; let d = c.bump(); d.get() as i32 - 5 }
"""), 0)

    def test_struct_consts_above_the_struct_and_alias_literals(self):
        self.assertEqual(_rs("""
type Alias = Real;
const G1: Guid = Guid { a: 3, b: 4 };
static G2: Guid = Guid { a: 5, b: 6 };
struct Guid { a: u32, b: u32 }
struct Real { x: u32 }
fn main() -> i32 { let r = Alias { x: 2 }; (G1.a + G2.b + r.x) as i32 - 11 }
"""), 0)

    def test_addr_of_and_qualified_fn_pointer(self):
        self.assertEqual(_rs("""
type Cb = unsafe extern "C" fn(u32) -> u32;
static mut COUNTER: u32 = 5;
fn twice(x: u32) -> u32 { x * 2 }
fn main() -> i32 {
    let f: Cb = twice;
    let p = core::ptr::addr_of!(COUNTER);
    let q = std::ptr::addr_of_mut!(COUNTER);
    unsafe { *q = 7; }
    (f(4) + unsafe { *p }) as i32 - 15
}
"""), 0)


class TestImplFnParameters(unittest.TestCase):

    def test_impl_fn_in_methods_and_inferred_closure_params(self):
        self.assertEqual(_rs("""
struct S { n: u32 }
impl S {
    fn find(&self, p: impl Fn(&S) -> bool) -> bool { p(self) }
    fn run(&self, d: impl FnOnce(bool, u64) -> bool) -> bool { d(true, 1) }
}
fn call(f: impl Fn(u32) -> u32, x: u32) -> u32 { f(x) }
fn main() -> i32 {
    let s = S { n: 5 };
    let k = 10u32;
    if !s.find(|x| x.n > 1) { return 1; }
    if !s.run(|a, b| a && b > 0) { return 2; }
    if call(|a| a + 1, 2) != 3 { return 3; }
    if call(|a| a + k, 2) != 12 { return 4; }
    0
}
"""), 0)


class TestDeferredNone(unittest.TestCase):
    """A bare `None` takes its type from a later use."""

    def test_let_none_fixed_by_a_later_assignment(self):
        self.assertEqual(_rs("""
fn step(n: u64) -> Option<u64> { if n < 4 { Some(n + 1) } else { None } }
fn find(id: u64) -> Option<(u64, Option<u64>)> {
    let mut current = Some(1_u64);
    let mut previous = None;
    for _ in 0..8 {
        let frame = current?;
        if frame == id { return Some((frame, previous)); }
        previous = Some(frame);
        current = step(frame);
    }
    None
}
fn main() -> i32 {
    let (a, b) = find(3).unwrap();
    let (c, d) = find(1).unwrap();
    if d.is_some() { return 1; }
    (a + b.unwrap() + c) as i32 - 6
}
"""), 0)

    def test_none_beside_some_in_if_and_match_arms(self):
        self.assertEqual(_rs("""
fn pick(flag: u64) -> u64 {
    let chosen = if flag == 0 { None } else { let v = flag * 2; Some(v) };
    match chosen { Some(v) => v, None => 0 }
}
fn arm(k: u64) -> u64 {
    let r = match k { 0 => None, 1 => Some(10_u64), _ => None };
    r.unwrap_or(7)
}
fn main() -> i32 { (pick(0) + pick(3) + arm(0) + arm(1) + arm(2)) as i32 - 30 }
"""), 0)

    def test_none_inside_tuples_of_an_if_chain(self):
        self.assertEqual(_rs("""
fn pair(k: u64) -> u64 {
    let (a, b, c) = if k == 0 {
        (1_u64, Some(2_u64), None)
    } else if k == 1 {
        (3_u64, None, Some((4_u64, 5_u64)))
    } else {
        (6_u64, None, Some((7_u64, 8_u64)))
    };
    let mut t = a;
    if let Some(x) = b { t += x; }
    if let Some((p, q)) = c { t += p + q; }
    t
}
fn main() -> i32 { (pair(0) + pair(1) + pair(2)) as i32 - 36 }
"""), 0)

    def test_a_none_nothing_types_is_still_an_error(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate("fn f() -> i32 {\n let x = None;\n 1\n}")
        self.assertIn("line 2: cannot infer the type of `None`",
                      str(cm.exception))


class TestUseAliases(unittest.TestCase):

    def test_alias_is_the_imported_name(self):
        c = crust.rename_use_aliases(
            "use crate::cap::{Handle, Table as CapTable};\n"
            "fn f() { let t = CapTable::new(); }\n")
        self.assertIn("let t = Table::new();", c)
        self.assertEqual(c.count("\n"), 2)

    def test_std_alias_and_clashing_name_are_left_alone(self):
        src = "use core::fmt::Result as FmtResult;\nfn f() -> FmtResult {}\n"
        self.assertEqual(crust.rename_use_aliases(src), src)
        src = ("use crate::a::Table as T2;\nstruct Table { n: i32 }\n"
               "fn f(t: T2) {}\n")
        self.assertEqual(crust.rename_use_aliases(src), src)


    def test_as_ref_on_a_reference_to_option_then_filter(self):
        self.assertEqual(_rs("""
struct D { id: u32 }
struct T { ds: [Option<D>; 3] }
impl T {
    fn get(&self, i: usize, id: u32) -> Option<&D> {
        self.ds.get(i)?.as_ref().filter(|d| d.id == id)
    }
}
fn main() -> i32 {
    let t = T { ds: [None, Some(D { id: 5 }), None] };
    if t.get(1, 5).is_none() { return 1; }
    if t.get(1, 6).is_some() { return 2; }
    if t.get(0, 5).is_some() { return 3; }
    0
}
"""), 0)

    def test_find_map_with_a_return_inside_the_closure(self):
        self.assertEqual(_rs("""
fn first_free(slots: &[Option<u32>; 4]) -> Option<(usize, u32)> {
    slots.iter().enumerate().find_map(|(i, s)| {
        if s.is_some() {
            return None;
        }
        Some((i, i as u32 * 10))
    })
}
fn main() -> i32 {
    let a: [Option<u32>; 4] = [Some(1), Some(2), None, None];
    let (i, v) = first_free(&a).unwrap();
    if i != 2 || v != 20 { return 1; }
    let full: [Option<u32>; 4] = [Some(1), Some(2), Some(3), Some(4)];
    if first_free(&full).is_some() { return 2; }
    0
}
"""), 0)

    def test_return_in_an_option_closure_leaves_only_the_closure(self):
        self.assertEqual(_rs("""
fn half(x: Option<u32>) -> u32 {
    let r = x.and_then(|v| {
        if v % 2 == 1 {
            return None;
        }
        Some(v / 2)
    });
    r.unwrap_or(99) + 1
}
fn main() -> i32 {
    if half(Some(8)) != 5 { return 1; }
    if half(Some(7)) != 100 { return 2; }
    if half(None) != 100 { return 3; }
    0
}
"""), 0)

    def test_integer_methods_through_a_reference_from_get(self):
        self.assertEqual(_rs("""
fn bump(v: &[u32; 3], i: usize) -> Option<u32> {
    let g = v.get(i)?.checked_add(1)?;
    Some(g)
}
fn main() -> i32 {
    let v = [1_u32, u32::MAX, 3];
    if bump(&v, 0).unwrap() != 2 { return 1; }
    if bump(&v, 1).is_some() { return 2; }
    if bump(&v, 5).is_some() { return 3; }
    0
}
"""), 0)

    def test_equality_on_structs_options_and_tuples(self):
        self.assertEqual(_rs("""
#[derive(Clone, Copy, PartialEq)]
struct P { a: u32, b: u32 }
#[derive(Clone, Copy, PartialEq)]
struct Q { p: P, o: Option<u32> }
fn main() -> i32 {
    let x = P { a: 1, b: 2 };
    let q = Q { p: x, o: Some(3) };
    if x != (P { a: 1, b: 2 }) { return 1; }
    if x == (P { a: 1, b: 3 }) { return 2; }
    if q != (Q { p: x, o: Some(3) }) { return 3; }
    if q == (Q { p: x, o: None }) { return 4; }
    let o: Option<P> = Some(x);
    if o != Some(P { a: 1, b: 2 }) { return 5; }
    if o == None { return 6; }
    let t = (1_u32, Some(2_u32));
    if t != (1, Some(2)) { return 7; }
    0
}
"""), 0)

    def test_if_let_as_a_value(self):
        self.assertEqual(_rs("""
fn pick(a: Option<u32>, b: u32) -> u32 {
    let v = if let Some(x) = a { x + 1 } else { b * 2 };
    let (p, q) = if let Some(y) = a { (y, Some(1_u32)) } else { (0, None) };
    v + p + q.unwrap_or(0)
}
fn main() -> i32 {
    if pick(Some(4), 10) != 10 { return 1; }
    if pick(None, 10) != 20 { return 2; }
    0
}
"""), 0)

    def test_flatten_over_options_and_untyped_array_of_options(self):
        self.assertEqual(_rs("""
struct D { id: u32 }
struct T { ds: [Option<D>; 4] }
impl T {
    fn has(&self, id: u32) -> bool { self.ds.iter().flatten().any(|d| d.id == id) }
    fn total(&self) -> u32 {
        let mut s = 0;
        for d in self.ds.iter().flatten() { s += d.id; }
        s
    }
}
fn main() -> i32 {
    let t = T { ds: [None, Some(D { id: 5 }), None, Some(D { id: 7 })] };
    if !t.has(7) || t.has(6) { return 1; }
    if t.total() != 12 { return 2; }
    let o = [Some(1_u32), None, Some(3)];
    let n: u32 = o.iter().flatten().sum();
    if n != 4 { return 3; }
    0
}
"""), 0)

    def test_untyped_none_repeat_array_fixed_through_a_pointer(self):
        self.assertEqual(_rs("""
fn make(n: u32) -> Option<u32> { if n > 1 { Some(n) } else { None } }
fn main() -> i32 {
    let mut slots = [None; 3];
    let mut k = 0;
    for slot in &mut slots {
        *slot = make(k);
        if slot.is_none() { k += 10; }
        k += 1;
    }
    let mut c = 0;
    for s in slots.iter() { if s.is_some() { c += 1; } }
    c - 2
}
"""), 0)

    def test_is_some_on_a_none_not_yet_assigned_and_saturating_mul(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let mut found = None;
    let mut hits = 0;
    for i in 0..5_u32 {
        if found.is_some() { hits += 1; }
        if i == 2 { found = Some(i); }
    }
    if found.is_none() { return 9; }
    let big = 4_000_000_000_u32.saturating_mul(3);
    if big != u32::MAX { return 8; }
    if 6_u64.saturating_mul(7) != 42 { return 7; }
    hits - 2
}
"""), 0)

    def test_open_none_typed_by_replace(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let mut dynamic = None;
    let a = 5_u32;
    if dynamic.replace(a).is_some() { return 1; }
    if dynamic.replace(a + 1).is_none() { return 2; }
    dynamic.unwrap() as i32 - 6
}
"""), 0)

    def test_generic_params_bounded_by_closure_traits(self):
        self.assertEqual(_rs("""
struct S { n: u32 }
impl S {
    fn run<L, C>(&mut self, limit_for: L, class_for: C) -> u32
    where
        L: Fn(u64) -> usize,
        C: Fn(u64) -> u32,
    {
        let a = limit_for(3).min(5) as u32;
        a + class_for(self.n as u64)
    }
    fn with<F>(&self, f: F) -> usize where F: FnOnce(&[u8]) -> usize {
        let d = [1_u8, 2, 3];
        f(&d)
    }
}
fn apply<F>(mut f: F) -> u32 where F: FnMut(u32) -> u32 { f(1) + f(2) }
fn main() -> i32 {
    let mut s = S { n: 4 };
    let k = 100_u32;
    let r = s.run(|d| d as usize * 2, |d| d as u32 + k);
    if r != 5 + 104 { return 1; }
    let len = s.with(|b| b.len());
    if len != 3 { return 2; }
    if apply(|x| x * 10) != 30 { return 3; }
    0
}
"""), 0)

    def test_closure_bound_signature_keeps_line_count(self):
        src = ("fn f<F>(\n    g: F,\n) -> u32\nwhere\n    F: Fn(u32) -> u32,\n"
               "{\n    g(1)\n}\n")
        out = crust.desugar_fn_bounds(src)
        self.assertEqual(out.count("\n"), src.count("\n"))
        self.assertIn("g: impl Fn(u32) -> u32", out)
        self.assertNotIn("where", out)

    def test_untyped_none_passed_by_mutable_reference(self):
        self.assertEqual(_rs("""
fn set_once<T>(slot: &mut Option<T>, value: T) -> Option<()> {
    if slot.is_some() { return None; }
    *slot = Some(value);
    Some(())
}
fn plain(slot: &mut Option<u32>) { *slot = Some(7); }
fn parse(a: u64, b: u64) -> Option<u64> {
    let mut x = None;
    let mut y = None;
    set_once(&mut x, a)?;
    plain2(&mut y);
    set_once(&mut x, b)?;
    Some(x? + y? as u64)
}
fn plain2(slot: &mut Option<u32>) { plain(slot); }
fn main() -> i32 {
    if parse(1, 2).is_some() { return 1; }
    let mut z = None;
    set_once(&mut z, 5_u64).unwrap();
    if set_once(&mut z, 6_u64).is_some() { return 2; }
    z.unwrap() as i32 - 5
}
"""), 0)

    def test_constructors_as_function_arguments(self):
        self.assertEqual(_rs("""
struct Handle(u32);
enum Root { Embedded(u32), Other }
fn make(n: u32) -> Option<u32> { if n > 0 { Some(n) } else { None } }
fn main() -> i32 {
    let h = make(4).map(Handle);
    if h.is_none() { return 1; }
    if h.unwrap().0 != 4 { return 2; }
    let r = make(5).map(Root::Embedded);
    if let Some(Root::Embedded(v)) = r { if v != 5 { return 3; } } else { return 4; }
    if make(0).map(Handle).is_some() { return 5; }
    0
}
"""), 0)

class TestModuleFileLayout(unittest.TestCase):

    def test_self_in_a_plain_module_file_reaches_its_child_directory(self):
        import os, tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "arch", "cpu"))
        for rel, text in {
            "lib.rs": "mod arch;\n",
            "arch.rs": "pub mod cpu;\n",
            "arch/cpu.rs": "mod inner;\npub use inner::index;\n",
            "arch/cpu/inner.rs": "pub fn index() -> usize { 3 }\n",
        }.items():
            with open(os.path.join(d, rel), "w") as f:
                f.write(text)
        out = crust.translate(
            "fn f() -> usize { let n = crate::arch::cpu::index(); n + 1 }\n",
            path=os.path.join(d, "user.rs"))
        self.assertIn("index", out)
        self.assertEqual(
            crust.resolve_use_path("self::inner",
                                   os.path.join(d, "arch", "cpu.rs"),
                                   strict=True),
            os.path.join(d, "arch", "cpu", "inner.rs"))


    def test_fill_with_none_and_nested_self_import(self):
        self.assertEqual(_rs("""
struct T { slots: [Option<u32>; 3], n: u32 }
impl T {
    fn clear(&mut self) { self.slots.fill(None); }
}
fn main() -> i32 {
    let mut t = T { slots: [Some(1), Some(2), None], n: 0 };
    t.clear();
    let mut c = 0;
    for s in t.slots.iter() { if s.is_some() { c += 1; } }
    c + t.n as i32
}
"""), 0)
        got = crust.find_use_imports(
            "use crate::{\n    initramfs::{self, Handle},\n};\n")
        self.assertEqual(got["initramfs"], "crate")
        self.assertEqual(got["Handle"], "crate::initramfs")

    def test_pointer_write_of_none_and_arrays_and_variant_shorthand(self):
        self.assertEqual(_rs("""
struct W { cur: Option<u32>, arr: [u32; 2], th: [Option<u32>; 2] }
enum R { Embedded(u32), Pub { slot: u8 } }
fn init(w: *mut W) {
    unsafe {
        core::ptr::addr_of_mut!((*w).cur).write(None);
        core::ptr::addr_of_mut!((*w).arr).write([4; 2]);
        for i in 0..2 { core::ptr::addr_of_mut!((*w).th[i]).write(None); }
    }
}
fn mk(slot: u8) -> R { R::Pub { slot } }
fn main() -> i32 {
    let mut w = W { cur: Some(1), arr: [0; 2], th: [Some(1), Some(2)] };
    init(&mut w);
    if w.cur.is_some() || w.arr[1] != 4 || w.th[0].is_some() { return 1; }
    if let R::Pub { slot } = mk(7) { if slot != 7 { return 2; } } else { return 3; }
    0
}
"""), 0)

    def test_checked_shifts(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let a = 3_u64;
    if a.checked_shl(32).unwrap() != 3_u64 << 32 { return 1; }
    if a.checked_shl(64).is_some() { return 2; }
    if 0x100_u32.checked_shr(4).unwrap() != 0x10 { return 3; }
    if 1_u32.checked_shr(32).is_some() { return 4; }
    0
}
"""), 0)

    def test_immediately_invoked_closure_with_question_marks(self):
        self.assertEqual(_rs("""
fn step(n: u32) -> Option<u32> { if n < 5 { Some(n + 1) } else { None } }
fn run(n: u32) -> bool {
    let ok = (|| {
        let a = step(n)?;
        let b = step(a)?;
        Some(a + b)
    })();
    ok.is_some()
}
fn main() -> i32 {
    if !run(1) { return 1; }
    if run(4) { return 2; }
    let v = (|| { 5 })();
    if v != 5 { return 3; }
    0
}
"""), 0)

class TestGinuxSmallForms(unittest.TestCase):
    """Small forms Ginux uses: unsafe-block receivers, array pointers, ..."""

    def test_small_forms_together(self):
        self.assertEqual(_rs(r"""
const N: usize = 4;
#[derive(Clone, Copy)]
struct E { a: u32, b: u32 }
impl E { const EMPTY: E = E { a: 7, b: 9 }; }
struct T { es: [E; N], fs: [Option<u32>; N], gs: [u8; N] }
fn mk() -> T { T { es: [E::EMPTY; N], fs: [None; N], gs: [3; N] } }
fn ptr_sum(msg: &[u16; 3]) -> u64 {
    let p = msg.as_ptr();
    unsafe { *p as u64 + *p.add(2) as u64 }
}
fn bytes_sum(b: &[u8]) -> u64 { let p = b.as_ptr(); unsafe { *p as u64 } }
fn ok(g: u32) -> Option<u32> { unsafe { g > 1 }.then_some(g) }
fn main() -> i32 {
    let t = mk();
    if t.es[3].a != 7 || t.es[0].b != 9 { return 1; }
    if t.fs[2].is_some() || t.gs[3] != 3 { return 2; }
    if ptr_sum(&[1, 2, 3]) != 4 { return 3; }
    if bytes_sum(&[5, 6]) != 5 { return 4; }
    if ok(1).is_some() || ok(2).unwrap() != 2 { return 5; }
    let mut buf = [0_u64; 2];
    unsafe { (buf.as_mut_ptr()).add(1).write(0x1234_u64.to_le()); }
    if buf[1] != 0x1234 { return 6; }
    if !b"ab\\n".ends_with(b"\\n") { return 7; }
    let (up, _): (u64, u64) = (5, 6);
    if up != 5 { return 8; }
    0
}
"""), 0)

    def test_byte_string_ends_with(self):
        self.assertEqual(_rs(r"""
fn main() -> i32 {
    if !b"ab\n".ends_with(b"\n") { return 1; }
    if !b"ab\n".starts_with(b"ab") { return 2; }
    if b"ab\n".ends_with(b"x") { return 3; }
    0
}
"""), 0)

    def test_array_field_from_array_value(self):
        self.assertEqual(_rs(r"""
const N: usize = 4;
struct S { a: [u8; N], b: u8 }
fn f(x: [u8; N]) -> S { S { a: x, b: 1 } }
fn main() -> i32 {
    let s = f([1, 2, 3, 4]);
    if s.a[2] != 3 { return 1; }
    0
}
"""), 0)

    def test_repeat_length_from_size_of(self):
        self.assertEqual(_rs(r"""
use core::mem::size_of;
#[derive(Clone, Copy)]
struct Entry { a: u64, b: u32 }
impl Entry { const EMPTY: Entry = Entry { a: 3, b: 4 }; }
const PAGE: usize = 128;
const PER: usize = (PAGE - 8) / size_of::<Entry>();
struct Page { entries: [Entry; PER], n: u8 }
fn mk() -> Page { Page { entries: [Entry::EMPTY; PER], n: 2 } }
fn main() -> i32 {
    let p = mk();
    if p.entries[PER - 1].a != 3 || p.n != 2 { return 1; }
    0
}
"""), 0)

    def test_method_paths_as_functions(self):
        self.assertEqual(_rs(r"""
fn half(x: usize) -> Option<usize> { if x % 2 == 0 { Some(x / 2) } else { None } }
fn main() -> i32 {
    let a: usize = 3;
    let r = a.checked_mul(2).and_then(usize::checked_next_power_of_two);
    if r != Some(8) { return 1; }
    let h = (4 as usize).checked_add(4).and_then(half);
    if h != Some(4) { return 2; }
    let mut v = [Some(1_u32), None];
    let s = v.get_mut(0).and_then(Option::as_mut);
    if s.is_none() { return 3; }
    0
}
"""), 0)

    def test_include_macro_splices_a_file(self):
        import os, tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "spec"))
        with open(os.path.join(d, "spec", "abi.rs"), "w") as f:
            f.write("pub const A: u16 = 1;\npub const B: u16 = 2;\n")
        src = ('include!("spec/abi.rs");\n'
               "fn f(x: u16) -> bool { matches!(x, A | B) }\n"
               "fn main() -> i32 { if f(2) && !f(3) { 0 } else { 1 } }\n")
        out = crust.translate(src, path=os.path.join(d, "m.rs"))
        self.assertIn("A = 1", out)

    def test_nested_arrays_with_unequal_dimensions(self):
        self.assertEqual(_rs(r"""
use core::mem::size_of;
#[derive(Clone, Copy)]
struct File { a: u64 }
const PAGE: usize = 128;
const PER: usize = (PAGE - 8) / size_of::<Option<File>>();
struct Pg { files: [Option<File>; PER], n: u8 }
fn mk() -> Pg { Pg { files: [None; PER], n: 2 } }
struct G { gdt: [[u64; 7]; PAGE], q: [[File; 2]; PAGE] }
fn main() -> i32 {
    let p = mk();
    if p.files[PER - 1].is_some() || p.n != 2 { return 1; }
    second()
}
#[derive(Clone, Copy)]
struct Q { v: u32 }
impl Q { const EMPTY: Q = Q { v: 9 }; }
const OUT: usize = 3;
struct W { gdt: [[u64; 7]; OUT], q: [[Q; 2]; PER] }
fn mkw() -> W { W { gdt: [[0; 7]; OUT], q: [[Q::EMPTY; 2]; PER] } }
fn second() -> i32 {
    let w = mkw();
    if w.gdt[2][6] != 0 { return 5; }
    if w.q[PER - 1][1].v != 9 { return 6; }
    if w.q[0][0].v != 9 { return 7; }
    0
}
"""), 0)

    def test_nested_array_dimension_order(self):
        self.assertEqual(_rs(r"""
fn main() -> i32 {
    let mut g = [[0_u8; 7]; 3];
    g[2][6] = 5;
    let h = [[1, 2, 3], [4, 5, 6]];
    if g[2][6] != 5 || h[1][2] != 6 || h[0][2] != 3 { return 1; }
    0
}
"""), 0)

    def test_tuple_patterns_in_parameters(self):
        self.assertEqual(_rs(r"""
fn sum((a, _): (u64, u64), k: u64) -> u64 { a + k }
struct S;
impl S {
    fn two(&self, (x, y): (u32, u32)) -> u32 { x * 10 + y }
}
fn main() -> i32 {
    if sum((5, 6), 2) != 7 { return 1; }
    if S.two((3, 4)) != 34 { return 2; }
    0
}
"""), 0)

    def test_refutable_and_nested_tuple_patterns(self):
        self.assertEqual(_rs(r"""
fn two(a: u32) -> Option<u32> { if a > 0 { Some(a) } else { None } }
fn f(x: u32, y: u32) -> u32 {
    let (Some(a), Some(b)) = (two(x), two(y)) else {
        return 0;
    };
    a + b
}
fn g() -> bool {
    let words = [(1_u32, 2_u32), (3, 4)];
    let expected = [1_u32, 3];
    expected.iter().enumerate().any(|(index, w)| *w == words[index].0)
}
fn h() -> u32 {
    let words = [(1_u32, 2_u32), (3, 4)];
    let mut n = 0;
    for (index, (a, _)) in words.iter().enumerate() { n += *a + index as u32; }
    n
}
fn main() -> i32 {
    if f(1, 2) != 3 || f(0, 2) != 0 { return 1; }
    if !g() { return 2; }
    if h() != 5 { return 3; }
    0
}
"""), 0)

    def test_trailing_comma_in_generic_arguments(self):
        self.assertEqual(_rs(r"""
fn f(x: u32) -> Result<
    (u32, u32),
    u8,
> {
    let o: Option<
        u32,
    > = Some(x);
    Ok((o.unwrap(), 2))
}
fn main() -> i32 { match f(4) { Ok((a, b)) => (a + b - 6) as i32, Err(_) => 1 } }
"""), 0)

    def test_attribute_on_an_erased_item_does_not_leak(self):
        self.assertEqual(_rs(r"""
#[cfg(test)]
extern crate std;
struct A { m: u8 }
impl A {
    const fn f(n: u8) -> Self {
        Self {
            m: n,
        }
    }
}
struct B<'a> { s: &'a [u8] }
impl<'a> B<'a> {
    const fn new() -> Self { Self { s: &[] } }
}
fn main() -> i32 {
    let b = B::new();
    if b.s.len() != 0 { return 2; }
    if A::f(3).m == 3 { 0 } else { 1 }
}
"""), 0)

    def test_as_chunks_and_as_chunks_mut(self):
        self.assertEqual(_rs(r"""
fn checksum(bytes: &[u8]) -> u32 {
    let mut sum = 0_u32;
    let (words, rest) = bytes.as_chunks::<2>();
    for word in words { sum += u32::from(u16::from_be_bytes([word[0], word[1]])); }
    if let Some(last) = rest.first() { sum += u32::from(*last) << 8; }
    sum
}
fn main() -> i32 {
    if checksum(&[0, 1, 0, 2, 3]) != 1 + 2 + 0x300 { return 1; }
    let mut out = [0_u8; 16];
    for c in out.as_chunks_mut::<8>().0 { c[0] = 7; }
    if out[8] != 7 || out[0] != 7 || out[1] != 0 { return 2; }
    extra()
}
fn extra() -> i32 {
    let mut out = [0_u8; 16];
    for c in out.as_chunks_mut::<8>().0 { c.copy_from_slice(&5_u64.to_ne_bytes()); }
    if out[0] != 5 || out[8] != 5 || out[9] != 0 { return 1; }
    0
}
"""), 0)

    def test_atomics_nested_in_a_static_struct(self):
        self.assertEqual(_rs(r"""
use core::sync::atomic::{AtomicBool, AtomicU64, Ordering};
struct Arch { enabled: AtomicBool }
static ARCH: Arch = Arch { enabled: AtomicBool::new(false) };
static COUNT: AtomicU64 = AtomicU64::new(0);
static COUNTS: [AtomicU64; 4] = [const { AtomicU64::new(0) }; 4];
fn main() -> i32 {
    ARCH.enabled.store(true, Ordering::Release);
    COUNT.fetch_add(3, Ordering::SeqCst);
    COUNTS[2].store(5, Ordering::Relaxed);
    if !ARCH.enabled.load(Ordering::Acquire) || COUNT.load(Ordering::SeqCst) != 3 { return 1; }
    if COUNTS[2].load(Ordering::Relaxed) != 5 { return 2; }
    0
}
"""), 0)

    def test_static_from_the_parent_module_is_extern(self):
        import os, tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "cpu"))
        with open(os.path.join(d, "cpu.rs"), "w") as f:
            f.write("pub static TICKS: u64 = 5;\n")
        out = crust.translate(
            "use super::TICKS;\nfn f() -> u64 { TICKS + 1 }\n",
            path=os.path.join(d, "cpu", "child.rs"))
        self.assertIn("extern unsigned long TICKS;", out)

    def test_asm_register_width_modifier(self):
        self.assertEqual(_rs(r"""
use core::arch::asm;
fn main() -> i32 {
    let v: u16;
    unsafe { asm!("mov {x:x}, 7", x = out(reg) v, options(nomem, nostack)); }
    if v != 7 { return 1; }
    0
}
"""), 0)

    def test_iterator_windows(self):
        self.assertEqual(_rs(r"""
fn main() -> i32 {
    let v = [1_u32, 3, 5, 9];
    if !v.windows(2).all(|p| p[0] < p[1]) { return 1; }
    let w = [1_u32, 3, 3];
    if w.windows(2).all(|p| p[0] < p[1]) { return 2; }
    let mut n = 0;
    for win in v.windows(3) { n += win[0] + win[2]; }
    if n != 1 + 5 + 3 + 9 { return 3; }
    if v.windows(5).count() != 0 { return 4; }
    let hay: &[u8] = b"xxabcxx";
    if !hay.windows(3).any(|win| win[0] == b'a' && win[2] == b'c') { return 5; }
    0
}
"""), 0)

    def test_iterator_flat_map_over_slices(self):
        self.assertEqual(_rs(r"""
#[derive(Clone, Copy)]
struct Layer { d: u32 }
struct Entry { live: bool, ls: [Layer; 2] }
impl Entry { fn layers(&self) -> &[Layer] { &self.ls } }
fn find(es: &[Entry], d: u32) -> Option<Layer> {
    es.iter().filter(|e| e.live).flat_map(Entry::layers).copied().find(|l| l.d == d)
}
fn main() -> i32 {
    let es = [Entry { live: true, ls: [Layer { d: 1 }, Layer { d: 2 }] },
              Entry { live: false, ls: [Layer { d: 3 }, Layer { d: 4 }] },
              Entry { live: true, ls: [Layer { d: 5 }, Layer { d: 6 }] }];
    if find(&es, 6).is_none() || find(&es, 2).is_none() { return 1; }
    if find(&es, 3).is_some() || find(&es, 9).is_some() { return 2; }
    let n = es.iter().flat_map(Entry::layers).count();
    if n != 6 { return 3; }
    0
}
"""), 0)

    def test_iterator_rposition(self):
        self.assertEqual(_rs(r"""
fn main() -> i32 {
    let v = [1_u32, 5, 3, 5, 2];
    if v.iter().rposition(|x| *x == 5) != Some(3) { return 1; }
    if v.iter().position(|x| *x == 5) != Some(1) { return 2; }
    if v.iter().rposition(|x| *x == 9).is_some() { return 3; }
    0
}
"""), 0)

    def test_glob_imported_consts_in_patterns(self):
        import os, tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "cpu"))
        with open(os.path.join(d, "cpu.rs"), "w") as f:
            f.write("mod abi;\npub(crate) use abi::*;\n")
        with open(os.path.join(d, "cpu", "abi.rs"), "w") as f:
            f.write("pub const A: u64 = 1;\npub const B: u64 = 2;\n")
        out = crust.translate(
            "use super::*;\n"
            "fn f(x: u64) -> bool { matches!(x, A | B) }\n",
            path=os.path.join(d, "cpu", "child.rs"))
        self.assertIn("A = 1", out)


if __name__ == "__main__":
    unittest.main()
