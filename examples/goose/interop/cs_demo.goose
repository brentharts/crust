// Goose functions a C# class calls (cs_demo.c).  Everything is a plain i64 so no marshalling.
export fn collatz_steps(n: i64) -> i64 {
    var steps = 0;
    var x = n;
    while x != 1 {
        x = if x % 2 == 0 { x / 2 } else { 3 * x + 1 };
        steps++;
    }
    return steps;
}

var seen = 0;
export fn count_call() -> i64 { seen++; return seen; }
