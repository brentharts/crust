#include <stdio.h>
#include <stdint.h>
typedef struct { char *base; int64_t len; int stk; } grow;
static char *top[2], *lim[2];
static char buf[64];
static void resize(grow *g, int64_t n) {
    g->len = 2;
    if (n < 0 || top[g->stk] + (n - g->len) * sizeof(int64_t) > lim[g->stk]) { printf("abort\n"); return; }
    printf("ok %ld\n", (long)n);
}
static void resize_k(grow *g) {
    g->len = 2;
    if (3 < 0 || top[g->stk] + (3 - g->len) * sizeof(int64_t) > lim[g->stk]) { printf("abort\n"); return; }
    printf("ok k\n");
}
int main(void) {
    grow g = {buf, 0, 1};
    top[1] = buf; lim[1] = buf + 64;
    resize(&g, 5);
    resize(&g, 100);
    resize_k(&g);
    return 0;
}
