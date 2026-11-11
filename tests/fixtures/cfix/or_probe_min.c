#include <stdio.h>
static char *top[2], *lim[2];
static char buf[64];
static int f(int k, int n) {
    if (3 < 0 || top[k] + n > lim[k]) return 1;
    return 0;
}
int main(void) {
    top[1] = buf; lim[1] = buf + 64;
    printf("%d %d\n", f(1, 5), f(1, 100));
    return 0;
}
