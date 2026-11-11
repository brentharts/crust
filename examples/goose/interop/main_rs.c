#include <stdio.h>
#include "both.goose"
#include "side.rs"
int main(void) {
    printf("%ld %ld %ld\n", (long)goose_combo(10), (long)rust_uses_goose(5), (long)goose_double(8));
    return 0;
}
