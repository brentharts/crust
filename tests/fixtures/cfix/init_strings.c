#include <stdio.h>
struct t { int a; char n[4]; };
static struct t g = {1, "abc"};
int main(void) { struct t x = {2, "xy"}; char m[2][4] = {"ab", "cd"}; printf("%d %s %d %s %s %s\n", x.a, x.n, g.a, g.n, m[0], m[1]); return 0; }
