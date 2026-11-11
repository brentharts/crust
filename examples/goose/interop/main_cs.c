#include <stdio.h>
long rust_triple(long x) { return x * 3; }     /* what both.goose's extern fn needs */
#include "both.goose"
#include "side.cs"
int main(void) {
    printf("%ld %ld\n", (long)goose_combo(10), (long)Side_CsUsesGoose(7));
    return 0;
}
