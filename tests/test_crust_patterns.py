"""Struct patterns, slice patterns, and general `let` patterns.

`match` used to accept literals, ranges, enum variants, `Option`/`Result` and
tuples. These cover the rest of what a pattern can be -- a plain struct, a
tuple struct, a slice or an array -- and the places a pattern may appear:
`let`, `let ... else`, `if let` and `while let`, all of which used to take only
`Some(name)`.
"""

import unittest

import shivyc.crust as crust
from tests.test_crust_extras import _rs


class TestStructPatterns(unittest.TestCase):

    def test_named_fields_with_literals_and_bindings(self):
        self.assertEqual(_rs("""
struct Point { x: i32, y: i32 }
fn f(p: Point) -> i32 {
    match p {
        Point { x: 0, y: 0 } => 0,
        Point { x, y: 0 } => 10 + x,
        Point { x: 0, y } => 20 + y,
        Point { x, y } => x * 100 + y,
    }
}
fn main() -> i32 {
    f(Point { x: 0, y: 0 }) + f(Point { x: 3, y: 0 })
        + f(Point { x: 0, y: 4 }) + f(Point { x: 1, y: 2 })
}
"""), (0 + 13 + 24 + 102) & 0xFF)

    def test_rest_pattern_and_renamed_binding(self):
        self.assertEqual(_rs("""
struct P { a: i32, b: i32, c: i32 }
fn main() -> i32 {
    let p = P { a: 1, b: 20, c: 3 };
    match p { P { b: mid, .. } => mid + 1 }
}
"""), 21)

    def test_tuple_struct(self):
        self.assertEqual(_rs("""
struct Pair(i32, i32);
fn f(p: Pair) -> i32 { match p { Pair(0, b) => b, Pair(a, b) => a + b } }
fn main() -> i32 { f(Pair(0, 7)) + f(Pair(2, 3)) }
"""), 12)

    def test_through_a_reference(self):
        self.assertEqual(_rs("""
struct Pair(i32, i32);
struct Point { x: i32, y: i32 }
fn f(p: &Pair) -> i32 { match p { Pair(0, b) => b, Pair(a, b) => a + b } }
fn g(p: &Point) -> i32 { match p { Point { x, y } => x - y } }
fn main() -> i32 {
    let a = Pair(1, 2);
    let b = Point { x: 9, y: 4 };
    f(&a) + g(&b)
}
"""), 8)

    def test_nested_struct_in_struct(self):
        self.assertEqual(_rs("""
struct In { v: i32 }
struct Out { a: In, b: i32 }
fn main() -> i32 {
    let o = Out { a: In { v: 5 }, b: 6 };
    match o {
        Out { a: In { v: 0 }, b } => b,
        Out { a: In { v }, b } => v * 10 + b,
    }
}
"""), 56)

    def test_struct_inside_option(self):
        self.assertEqual(_rs("""
struct P { x: i32, y: i32 }
fn f(o: Option<P>) -> i32 {
    match o { Some(P { x, y }) => x + y, None => -1 }
}
fn main() -> i32 { f(Some(P { x: 2, y: 3 })) + f(None) + 10 }
"""), 14)

    def test_guard_sees_the_bindings(self):
        self.assertEqual(_rs("""
struct P { x: i32, y: i32 }
fn f(p: P) -> i32 {
    match p { P { x, y } if x > y => 1, P { .. } => 2 }
}
fn main() -> i32 { f(P { x: 5, y: 1 }) * 10 + f(P { x: 1, y: 5 }) }
"""), 12)

    def test_matches_macro(self):
        self.assertEqual(_rs("""
struct P { x: i32, y: i32 }
fn main() -> i32 {
    let p = P { x: 0, y: 3 };
    let a = matches!(p, P { x: 0, .. });
    let b = matches!(p, P { y: 0, .. });
    if a && !b { 1 } else { 0 }
}
"""), 1)

    def test_generic_struct_instance(self):
        self.assertEqual(_rs("""
struct W<T> { v: T }
fn main() -> i32 {
    let w: W<i32> = W { v: 41 };
    match w { W { v } => v + 1 }
}
"""), 42)

    def test_unknown_field_is_refused(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate("""
struct P { x: i32 }
fn f(p: P) -> i32 { match p { P { z } => z } }
""")
        self.assertIn("no field `z`", str(cm.exception))

    def test_tuple_pattern_on_a_named_struct_is_refused(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate("""
struct P { x: i32 }
fn f(p: P) -> i32 { match p { P(a) => a } }
""")
        self.assertIn("not a tuple struct", str(cm.exception))


class TestSlicePatterns(unittest.TestCase):

    _PRELUDE = """
fn f(s: &[i32]) -> i32 {
    match s {
        [] => 100,
        [a] => *a,
        [a, b] => a + b,
        [first, .., last] => first * 10 + last,
    }
}
"""

    def test_by_length_and_ends(self):
        self.assertEqual(_rs(self._PRELUDE + """
fn main() -> i32 {
    let v = [4, 5, 6, 7];
    f(&v[0..0]) + f(&v[0..1]) + f(&v[0..2]) + f(&v[0..4])
}
"""), (100 + 4 + 9 + 47) & 0xFF)

    def test_head_and_rest(self):
        self.assertEqual(_rs("""
fn f(s: &[i32]) -> i32 {
    match s { [head, rest @ ..] => head + rest.len() as i32, [] => 0 }
}
fn main() -> i32 {
    let v = [10, 1, 2, 3];
    f(&v[0..4]) + f(&v[0..0])
}
"""), 13)

    def test_rest_is_a_usable_slice(self):
        self.assertEqual(_rs("""
fn sum(s: &[i32]) -> i32 {
    match s { [] => 0, [h, t @ ..] => h + sum(t) }
}
fn main() -> i32 {
    let v = [1, 2, 3, 4, 5];
    sum(&v[0..5])
}
"""), 15)

    def test_middle_rest_and_literals(self):
        self.assertEqual(_rs("""
fn f(s: &[i32]) -> i32 {
    match s {
        [1, .., 9] => 1,
        [1, ..] => 2,
        [.., 9] => 3,
        _ => 4,
    }
}
fn main() -> i32 {
    let a = [1, 5, 9];
    let b = [1, 5, 6];
    let c = [2, 5, 9];
    let d = [2, 5, 6];
    f(&a[0..3]) * 1000 / 1000 + f(&b[0..3]) * 10 + f(&c[0..3]) * 100
        + f(&d[0..3]) * 0
}
"""), (1 + 20 + 300) & 0xFF)

    def test_array_pattern(self):
        self.assertEqual(_rs("""
fn f(a: [i32; 3]) -> i32 {
    match a { [1, _, z] => z, [x, y, z] => x + y + z }
}
fn main() -> i32 {
    let a = [1, 2, 30];
    let b = [2, 3, 4];
    f(a) + f(b)
}
"""), 39)

    def test_let_array_destructure(self):
        self.assertEqual(_rs("""
fn main() -> i32 {
    let a = [3, 4, 5];
    let [x, y, z] = a;
    x * 100 + y * 10 + z - 300
}
"""), 45)

    def test_pattern_length_mismatch_on_array_is_refused(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate("""
fn f(a: [i32; 3]) -> i32 { match a { [x, y] => x + y, _ => 0 } }
""")
        self.assertIn("array", str(cm.exception))

    def test_slice_pattern_on_a_non_slice_is_refused(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate("""
fn f(x: i32) -> i32 { match x { [a] => 1, _ => 0 } }
""")
        self.assertIn("slice or an array", str(cm.exception))

    def test_two_rests_are_refused(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate("""
fn f(s: &[i32]) -> i32 { match s { [.., a, ..] => 1, _ => 0 } }
""")
        self.assertIn("only one `..`", str(cm.exception))


class TestLetPatterns(unittest.TestCase):

    def test_let_struct_destructure(self):
        self.assertEqual(_rs("""
struct P { x: i32, y: i32 }
fn main() -> i32 {
    let p = P { x: 6, y: 7 };
    let P { x, y } = p;
    x * y
}
"""), 42)

    def test_let_tuple_struct_destructure(self):
        self.assertEqual(_rs("""
struct Pair(i32, i32);
fn main() -> i32 {
    let q = Pair(5, 6);
    let Pair(a, b) = q;
    a + b
}
"""), 11)

    def test_let_else_diverges_when_the_pattern_fails(self):
        self.assertEqual(_rs("""
fn f(o: Option<i32>) -> i32 {
    let Some(v) = o else { return 50; };
    v + 1
}
fn main() -> i32 { f(Some(4)) + f(None) }
"""), 55)

    def test_let_else_on_a_struct_inside_option(self):
        self.assertEqual(_rs("""
struct P { x: i32, y: i32 }
fn f(o: Option<P>) -> i32 {
    let Some(P { x, y }) = o else { return -1; };
    x * 10 + y
}
fn main() -> i32 { f(Some(P { x: 2, y: 3 })) + f(None) + 1 }
"""), 23)

    def test_let_else_on_a_slice(self):
        self.assertEqual(_rs("""
fn f(s: &[i32], d: i32) -> i32 {
    let [first, ..] = s else { return d; };
    first
}
fn main() -> i32 {
    let v = [8, 9];
    f(&v[0..2], 99) + f(&v[0..0], 1)
}
"""), 9)

    def test_refutable_let_without_else_is_refused(self):
        with self.assertRaises(crust.CrustError) as cm:
            crust.translate("""
fn f(o: Option<i32>) -> i32 { let Some(v) = o; v }
""")
        self.assertIn("can fail to match", str(cm.exception))

    def test_if_let_on_a_data_enum_variant(self):
        self.assertEqual(_rs("""
enum Shape { Circle(i32), Rect { w: i32, h: i32 }, Empty }
fn area(s: Shape) -> i32 {
    if let Shape::Circle(r) = s { return 3 * r * r; }
    if let Shape::Rect { w, h } = s { return w * h; }
    0
}
fn main() -> i32 {
    area(Shape::Circle(2)) + area(Shape::Rect { w: 3, h: 5 })
        + area(Shape::Empty)
}
"""), 27)

    def test_if_let_ok_and_err_with_else(self):
        self.assertEqual(_rs("""
fn parse(n: i32) -> Result<i32, i32> {
    if n > 0 { Ok(n) } else { Err(-n) }
}
fn main() -> i32 {
    let mut r = 0;
    if let Ok(v) = parse(7) { r += v; }
    if let Err(e) = parse(-9) { r += e; } else { r += 100; }
    if let Ok(_) = parse(-1) { r += 100; } else { r += 1; }
    r
}
"""), 17)

    def test_if_let_else_if_let_chain(self):
        self.assertEqual(_rs("""
fn f(r: Result<i32, i32>) -> i32 {
    if let Ok(v) = r { v } else if let Err(e) = r { e + 100 } else { 0 }
}
fn main() -> i32 { f(Ok(3)) + f(Err(4)) }
"""), 3 + 104)

    def test_if_let_struct_pattern(self):
        self.assertEqual(_rs("""
struct P { x: i32, y: i32 }
fn main() -> i32 {
    let p = P { x: 0, y: 9 };
    let mut r = 0;
    if let P { x: 0, y } = p { r += y; }
    if let P { x: 1, y } = p { r += 100 + y; }
    r
}
"""), 9)

    def test_while_let_with_a_general_pattern(self):
        self.assertEqual(_rs("""
fn parse(n: i32) -> Result<i32, i32> {
    if n > 0 { Ok(n) } else { Err(-n) }
}
fn main() -> i32 {
    let mut r = 0;
    let mut n = 4;
    while let Ok(k) = parse(n) { r += k; n -= 1; }
    r
}
"""), 10)

    def test_while_let_break_and_continue(self):
        self.assertEqual(_rs("""
fn parse(n: i32) -> Result<i32, i32> {
    if n > 0 { Ok(n) } else { Err(-n) }
}
fn main() -> i32 {
    let mut r = 0;
    let mut n = 6;
    while let Ok(k) = parse(n) {
        n -= 1;
        if k == 5 { continue; }
        if k == 2 { break; }
        r += k;
    }
    r
}
"""), 6 + 4 + 3)


if __name__ == "__main__":
    unittest.main()
