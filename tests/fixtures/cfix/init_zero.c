#include <stdio.h>
typedef struct { long n; long data[4]; } node;
typedef struct { long data[4]; long n; } node2;
struct two { node a; long b; };
int main(void) {
    node x = {0};
    node2 y = {0};
    struct two t = {0};
    node arr[2] = {0};
    long z[3][2] = {0};
    printf("%ld %ld %ld %ld %ld %ld\n", x.n, x.data[3], y.data[2], y.n, t.a.data[1], arr[1].data[3] + z[2][1]);
    return 0;
}
