"""`dyn Trait`: trait objects behind a reference or raw pointer.

`&dyn Trait` is a fat pointer -- the data pointer and a vtable -- so a call is
an indirect call through it, where a generic `T: Trait` is a direct one. The
vtable for a (trait, type) pair is generated only when that type is coerced to
the trait object somewhere.
"""

import unittest

import shivyc.crust as crust
from tests.test_crust_extras import _rs

_SHAPES = """
trait Shape {
    fn area(&self) -> i32;
    fn scale(&mut self, k: i32);
    fn name_len(&self) -> i32 { 6 }
}
struct Circle { r: i32 }
struct Rect { w: i32, h: i32 }
impl Shape for Circle {
    fn area(&self) -> i32 { 3 * self.r * self.r }
    fn scale(&mut self, k: i32) { self.r *= k; }
}
impl Shape for Rect {
    fn area(&self) -> i32 { self.w * self.h }
    fn scale(&mut self, k: i32) { self.w *= k; self.h *= k; }
    fn name_len(&self) -> i32 { 4 }
}
"""


class TestDynDispatch(unittest.TestCase):

    def test_call_through_a_reference_parameter(self):
        self.assertEqual(_rs(_SHAPES + """
fn total(a: &dyn Shape, b: &dyn Shape) -> i32 { a.area() + b.area() }
fn main() -> i32 {
    let c = Circle { r: 2 };
    let r = Rect { w: 3, h: 4 };
    total(&c, &r)
}
"""), 24)

    def test_different_types_reach_their_own_method(self):
        self.assertEqual(_rs(_SHAPES + """
fn area(s: &dyn Shape) -> i32 { s.area() }
fn main() -> i32 {
    let c = Circle { r: 1 };
    let r = Rect { w: 5, h: 5 };
    area(&c) * 100 + area(&r)
}
"""), (3 * 100 + 25) & 0xFF)

    def test_mutable_trait_object(self):
        self.assertEqual(_rs(_SHAPES + """
fn grow(s: &mut dyn Shape) { s.scale(2); }
fn main() -> i32 {
    let mut c = Circle { r: 2 };
    grow(&mut c);
    c.r
}
"""), 4)

    def test_default_method_and_override(self):
        self.assertEqual(_rs(_SHAPES + """
fn n(s: &dyn Shape) -> i32 { s.name_len() }
fn main() -> i32 {
    let c = Circle { r: 1 };
    let r = Rect { w: 1, h: 1 };
    n(&c) * 10 + n(&r)
}
"""), 64)

    def test_let_annotation_and_as_cast(self):
        self.assertEqual(_rs(_SHAPES + """
fn main() -> i32 {
    let c = Circle { r: 2 };
    let r = Rect { w: 2, h: 3 };
    let a: &dyn Shape = &c;
    let b = &r as &dyn Shape;
    a.area() + b.area()
}
"""), 18)

    def test_array_of_trait_objects(self):
        self.assertEqual(_rs(_SHAPES + """
fn main() -> i32 {
    let c = Circle { r: 1 };
    let r = Rect { w: 2, h: 2 };
    let all: [&dyn Shape; 2] = [&c as &dyn Shape, &r as &dyn Shape];
    let mut sum = 0;
    for s in all { sum += s.area(); }
    sum
}
"""), 7)

    def test_struct_field_holds_a_trait_object(self):
        self.assertEqual(_rs(_SHAPES + """
struct Holder<'a> { shape: &'a dyn Shape, extra: i32 }
fn main() -> i32 {
    let r = Rect { w: 3, h: 3 };
    let h = Holder { shape: &r, extra: 1 };
    h.shape.area() + h.extra
}
"""), 10)

    def test_returning_a_trait_object(self):
        self.assertEqual(_rs(_SHAPES + """
fn pick<'a>(use_circle: bool, c: &'a Circle, r: &'a Rect) -> &'a dyn Shape {
    if use_circle { c } else { r }
}
fn main() -> i32 {
    let c = Circle { r: 1 };
    let r = Rect { w: 4, h: 4 };
    pick(true, &c, &r).area() + pick(false, &c, &r).area()
}
"""), 19)

    def test_send_and_lifetime_bounds_are_accepted(self):
        self.assertEqual(_rs(_SHAPES + """
fn f(s: &(dyn Shape + Send)) -> i32 { s.area() }
fn g<'a>(s: &'a (dyn Shape + 'a)) -> i32 { s.area() }
fn main() -> i32 {
    let r = Rect { w: 2, h: 5 };
    f(&r) + g(&r)
}
"""), 20)

    def test_raw_pointer_trait_object(self):
        self.assertEqual(_rs(_SHAPES + """
fn main() -> i32 {
    let r = Rect { w: 2, h: 2 };
    let p: *const dyn Shape = &r;
    unsafe { (*p).area() }
}
"""), 4)

    def test_trait_object_passed_on_to_another_function(self):
        self.assertEqual(_rs(_SHAPES + """
fn inner(s: &dyn Shape) -> i32 { s.area() }
fn outer(s: &dyn Shape) -> i32 { inner(s) + 1 }
fn main() -> i32 { let c = Circle { r: 2 }; outer(&c) }
"""), 13)


class TestDynSupertraits(unittest.TestCase):

    def test_supertrait_methods_are_callable_on_the_subtrait_object(self):
        self.assertEqual(_rs("""
trait Named { fn id(&self) -> i32; }
trait Animal: Named { fn legs(&self) -> i32; }
struct Dog { n: i32 }
impl Named for Dog { fn id(&self) -> i32 { self.n } }
impl Animal for Dog { fn legs(&self) -> i32 { 4 } }
fn f(a: &dyn Animal) -> i32 { a.id() * 10 + a.legs() }
fn main() -> i32 { let d = Dog { n: 7 }; f(&d) }
"""), 74)


_OWNED = """
static mut DROPS: i32 = 0;
trait Shape { fn area(&self) -> i32; }
struct Circle { r: i32 }
impl Shape for Circle { fn area(&self) -> i32 { self.r } }
impl Drop for Circle { fn drop(&mut self) { unsafe { DROPS += 1; } } }
fn drops() -> i32 { unsafe { DROPS } }
"""


class TestBoxDyn(unittest.TestCase):
    """`Box<dyn Trait>` owns its value and runs the concrete Drop once."""

    def test_call_through_a_box(self):
        self.assertEqual(_rs(_OWNED + """
fn make(r: i32) -> Box<dyn Shape> { Box::new(Circle { r: r }) }
fn main() -> i32 { let b = make(7); b.area() }
"""), 7)

    def test_drop_runs_once_at_scope_exit(self):
        self.assertEqual(_rs(_OWNED + """
fn main() -> i32 {
    {
        let a: Box<dyn Shape> = Box::new(Circle { r: 1 });
        let b: Box<dyn Shape> = Box::new(Circle { r: 2 });
        if a.area() + b.area() != 3 { return 99; }
    }
    drops()
}
"""), 2)

    def test_move_from_a_named_box_does_not_double_drop(self):
        self.assertEqual(_rs(_OWNED + """
fn main() -> i32 {
    {
        let c = Box::new(Circle { r: 3 });
        let d: Box<dyn Shape> = c;
        if d.area() != 3 { return 99; }
    }
    drops()
}
"""), 1)

    def test_moved_into_a_callee(self):
        self.assertEqual(_rs(_OWNED + """
fn consume(s: Box<dyn Shape>) -> i32 { s.area() }
fn main() -> i32 {
    let e: Box<dyn Shape> = Box::new(Circle { r: 4 });
    let a = consume(e);
    a * 10 + drops()
}
"""), 41)

    def test_struct_field(self):
        self.assertEqual(_rs(_OWNED + """
struct Holder { s: Box<dyn Shape>, n: i32 }
fn main() -> i32 {
    {
        let h = Holder { s: Box::new(Circle { r: 5 }), n: 1 };
        if h.s.area() + h.n != 6 { return 99; }
    }
    drops()
}
"""), 1)

    def test_vec_of_boxes(self):
        self.assertEqual(_rs(_OWNED + """
fn main() -> i32 {
    let mut sum = 0;
    {
        let mut v: Vec<Box<dyn Shape>> = Vec::new();
        v.push(Box::new(Circle { r: 1 }));
        v.push(Box::new(Circle { r: 2 }));
        v.push(Box::new(Circle { r: 3 }));
        for s in v.iter() { sum += s.area(); }
    }
    sum * 10 + drops()
}
"""), 63)


class TestDynRefusals(unittest.TestCase):

    def test_type_that_does_not_implement_the_trait(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate(_SHAPES + """
struct Other { x: i32 }
fn f(s: &dyn Shape) -> i32 { s.area() }
fn main() -> i32 { let o = Other { x: 1 }; f(&o) }
""")
        self.assertIn("does not implement `Shape`", str(cm.exception))

    def test_by_value_self_is_not_callable_on_a_trait_object(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate("""
trait Eat { fn eat(self) -> i32; }
struct A { x: i32 }
impl Eat for A { fn eat(self) -> i32 { self.x } }
fn f(e: &dyn Eat) -> i32 { e.eat() }
""")
        self.assertIn("by value", str(cm.exception))

    def test_unknown_method(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate(_SHAPES + """
fn f(s: &dyn Shape) -> i32 { s.perimeter() }
""")
        self.assertIn("no method `perimeter` on `dyn Shape`", str(cm.exception))

    def test_unknown_trait(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate("""
fn f(s: &dyn Missing) -> i32 { 0 }
""")
        self.assertIn("no trait `Missing`", str(cm.exception))

    def test_dyn_fn_is_a_closure_object_not_a_vtable(self):
        # `dyn Fn` is supported (see test_crust_closure_objects); it must
        # not be mistaken for a user trait.
        c = crust.translate("""
fn f(g: &dyn Fn(i32) -> i32) -> i32 { g(1) }
""")
        self.assertIn("crust_fnbox_int_int", c)
        self.assertNotIn("crust_vt_Fn", c)


class TestDynCodegen(unittest.TestCase):

    def test_vtable_is_only_generated_for_coerced_types(self):
        c = crust.translate(_SHAPES + """
fn area(s: &dyn Shape) -> i32 { s.area() }
fn main() -> i32 { let c = Circle { r: 1 }; area(&c) }
""")
        self.assertIn("crust_vtinst_Shape_Circle", c)
        self.assertNotIn("crust_vtinst_Shape_Rect", c)

    def test_unused_dyn_trait_costs_nothing(self):
        c = crust.translate(_SHAPES + "fn main() -> i32 { 0 }\n")
        self.assertNotIn("crust_vt_Shape", c)


if __name__ == "__main__":
    unittest.main()
