import std;
import base64;
fn main() {
    var out: u8[>..] = [];
    base64::encode(out, "hello, goose");
    print(out);
    var back: u8[>..] = [];
    let ok = base64::decode(back, out);
    print(ok, " ", back);
}
