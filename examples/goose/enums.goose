enum Shape { Circle { r: f64 }, Rect { w: f64, h: f64 }, Dot }
fn area(s: Shape.Circle) -> f64 { 3.0 * s.r * s.r }
fn area(s: Shape.Rect) -> f64 { s.w * s.h }
fn area(s: Shape.Dot) -> f64 { 0.0 }
fn name(s: Shape) -> u8[:] {
    match s {
        Circle c => "circle",
        Rect r => if r.w == r.h { "square" } else { "rectangle" },
        _ => "other",
    }
}
fn scaled(s: Shape, k: f64) -> Shape {
    match s {
        Circle c => Shape.Circle { r: c.r * k },
        Rect r => Shape.Rect { w: r.w * k, h: r.h * k },
        Dot => Shape.Dot,
    }
}
fn main() {
    var shapes: Shape[>..] = [];
    shapes.push(Shape.Circle { r: 1.0 });
    shapes.push(Shape.Rect { w: 2.0, h: 3.0 });
    shapes.push(Shape.Dot);
    shapes.push(Shape.Rect { w: 2.0, h: 2.0 });
    var total = 0.0;
    for s in shapes { print(name(s), " ", s, " area ", area(s)); total += area(s); }
    print(total);
    for &s in shapes { s = scaled(s, 2.0); }
    print(shapes);
    print(shapes);
    let d = Shape.Dot;
    print(d, " ", Shape.Circle { r: 2.5 });
    var x: Shape = Shape.Dot;
    x = Shape.Rect { w: 1.0, h: 1.0 };
    print(x);
    let k = match x { Rect r => r.w + r.h, _ => 0.0 };
    print(k);
}
