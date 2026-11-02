"""Returning closures: `-> impl Fn(..)`, `Box<dyn Fn(..)>` and `&dyn Fn(..)`.

A closure that outlives its frame needs its environment on the heap, so all
three spellings lower to one owning object, `{ env, call, drop }`, with a
generated `T_drop` that runs the captured values' destructors and frees the
environment. `&dyn Fn` borrows: the object is a temporary that points at the
closure's own environment and frees nothing.
"""

import unittest

import shivyc.crust as crust
from tests.test_crust_extras import _rs

_TOK = """
static mut DROPS: i32 = 0;
struct Tok { v: i32 }
impl Drop for Tok { fn drop(&mut self) { unsafe { DROPS += 1; } } }
fn drops() -> i32 { unsafe { DROPS } }
"""


class TestReturningClosures(unittest.TestCase):

    def test_impl_fn_return_captures_by_move(self):
        self.assertEqual(_rs("""
fn make_adder(n: i32) -> impl Fn(i32) -> i32 { move |x: i32| x + n }
fn main() -> i32 { let a = make_adder(3); a(1) + a(10) }
"""), 4 + 13)

    def test_boxed_dyn_fn_return(self):
        self.assertEqual(_rs("""
fn boxed(n: i32) -> Box<dyn Fn(i32) -> i32> { Box::new(move |x: i32| x * n) }
fn main() -> i32 { boxed(4)(5) }
"""), 20)

    def test_fn_mut_keeps_its_state_between_calls(self):
        self.assertEqual(_rs("""
fn counter() -> impl FnMut() -> i32 {
    let mut n = 0;
    move || { n += 1; n }
}
fn main() -> i32 { let mut c = counter(); c(); c(); c() }
"""), 3)

    def test_two_closures_from_one_function_are_independent(self):
        self.assertEqual(_rs("""
fn counter() -> impl FnMut() -> i32 {
    let mut n = 0;
    move || { n += 1; n }
}
fn main() -> i32 {
    let mut a = counter();
    let mut b = counter();
    a(); a(); a();
    b() * 10 + a()
}
"""), 14)

    def test_composition_returns_a_closure_of_closures(self):
        self.assertEqual(_rs("""
fn compose(f: impl Fn(i32) -> i32, g: impl Fn(i32) -> i32) -> impl Fn(i32) -> i32 {
    move |x: i32| g(f(x))
}
fn main() -> i32 {
    let k = compose(|x: i32| x + 1, |x: i32| x * 10);
    k(4)
}
"""), 50)

    def test_a_returned_closure_passes_to_a_generic_function(self):
        self.assertEqual(_rs("""
fn compose(f: impl Fn(i32) -> i32, g: impl Fn(i32) -> i32) -> impl Fn(i32) -> i32 {
    move |x: i32| g(f(x))
}
fn run<F: Fn(i32) -> i32>(f: F, x: i32) -> i32 { f(x) }
fn main() -> i32 { run(compose(|x: i32| x + 2, |x: i32| x + 3), 1) }
"""), 6)

    def test_calling_the_result_of_a_call_directly(self):
        self.assertEqual(_rs("""
fn adder(n: i32) -> impl Fn(i32) -> i32 { move |x: i32| x + n }
fn main() -> i32 { adder(1)(2) + adder(10)(20) }
"""), 33)


class TestClosureObjectStorage(unittest.TestCase):

    def test_struct_field(self):
        self.assertEqual(_rs("""
struct Button { on_click: Box<dyn Fn(i32) -> i32>, id: i32 }
fn main() -> i32 {
    let b = Button { on_click: Box::new(|x: i32| x - 1), id: 2 };
    (b.on_click)(10) + b.id
}
"""), 11)

    def test_vec_of_boxed_closures(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let mut fs: Vec<Box<dyn Fn(i32) -> i32>> = Vec::new();
    fs.push(Box::new(|x: i32| x + 1));
    fs.push(Box::new(|x: i32| x * 2));
    let mut s = 0;
    for g in fs.iter() { s += g(5); }
    s
}
"""), 16)

    def test_capturing_closure_boxed_from_a_local(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let off = 10;
    let c = |x: i32| x + off;
    let b: Box<dyn Fn(i32) -> i32> = Box::new(c);
    b(5)
}
"""), 15)


class TestBorrowedClosureObjects(unittest.TestCase):

    def test_ref_dyn_fn_parameter_takes_a_closure_by_reference(self):
        self.assertEqual(_rs("""
fn apply(f: &dyn Fn(i32) -> i32, v: i32) -> i32 { f(v) }
fn main() -> i32 {
    let off = 10;
    let c = |x: i32| x + off;
    apply(&c, 5) + apply(&c, 1)
}
"""), 15 + 11)

    def test_ref_dyn_fn_takes_a_returned_closure(self):
        self.assertEqual(_rs("""
fn adder(n: i32) -> impl Fn(i32) -> i32 { move |x: i32| x + n }
fn apply(f: &dyn Fn(i32) -> i32, v: i32) -> i32 { f(v) }
fn main() -> i32 { let a = adder(3); apply(&a, 4) + apply(&a, 5) }
"""), 7 + 8)

    def test_fn_mut_through_a_mutable_reference(self):
        self.assertEqual(_rs("""
fn twice(f: &mut dyn FnMut() -> i32) -> i32 { f(); f() }
fn main() -> i32 {
    let mut n = 0;
    let mut inc = || { n += 5; n };
    twice(&mut inc)
}
"""), 10)


class TestClosureObjectOwnership(unittest.TestCase):
    """What a `move` closure owns is dropped exactly once, with the object."""

    def test_captured_value_dropped_when_the_closure_goes_out_of_scope(self):
        self.assertEqual(_rs(_TOK + """
fn holder(t: Tok) -> impl Fn() -> i32 { move || t.v }
fn main() -> i32 {
    let mut bad = 0;
    {
        let h = holder(Tok { v: 7 });
        if h() != 7 { bad += 1; }
        if drops() != 0 { bad += 2; }
    }
    if drops() != 1 { bad += 4; }
    bad
}
"""), 0)

    def test_moved_into_a_callee_is_dropped_there_once(self):
        self.assertEqual(_rs(_TOK + """
fn holder(t: Tok) -> impl Fn() -> i32 { move || t.v }
fn eat<F: Fn() -> i32>(f: F) -> i32 { f() }
fn main() -> i32 {
    let h = holder(Tok { v: 5 });
    let r = eat(h);
    r * 10 + drops()
}
"""), 51)

    def test_boxed_move_closure_drops_its_capture(self):
        self.assertEqual(_rs(_TOK + """
fn main() -> i32 {
    {
        let t = Tok { v: 3 };
        let b: Box<dyn Fn() -> i32> = Box::new(move || t.v);
        if b() != 3 { return 99; }
    }
    drops()
}
"""), 1)


class TestClosureObjectCodegen(unittest.TestCase):

    def test_plain_generic_closures_stay_allocation_free(self):
        c = crust.translate("""
fn run<F: Fn(i32) -> i32>(f: F, x: i32) -> i32 { f(x) }
fn main() -> i32 { let o = 3; run(|x: i32| x + o, 1) }
""")
        self.assertNotIn("malloc", c)
        self.assertNotIn("crust_fnbox", c)

    def test_non_fn_impl_trait_return_is_refused(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate("fn f() -> impl Iterator<Item = i32> { 0..3 }")
        self.assertIn("impl Iterator", str(cm.exception))


class TestGenericCallFixes(unittest.TestCase):
    """Two fixes this work needed in generic calls."""

    def test_unannotated_let_takes_the_instantiated_return_type(self):
        self.assertEqual(_rs("""
fn pick<T>(a: T, _b: T) -> T { a }
fn main() -> i32 { let x = pick(4, 9); x }
"""), 4)


if __name__ == "__main__":
    unittest.main()
