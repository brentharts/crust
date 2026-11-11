import std;
import csv;
fn main() {
    let text = "a,b,\"c,d\"\n1,2,3\n";
    let ok = csv::each_record(text) { fields =>
        print(fields.len, " fields");
    };
    print(ok);
    var out: u8[>..] = [];
    csv::format_field(out, "x,y");
    print(out);
}
