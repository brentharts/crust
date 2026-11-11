import math;
import os;
fn main() {
    let a = sqrt(2.0);
    let t0 = clock_ns();
    let f: f32 = 2.0;
    print(a, sqrt(f), floor(2.7), pow(2.0, 10.0));
    print(clock_ns() >= t0);
}
