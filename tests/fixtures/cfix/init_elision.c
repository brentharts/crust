#include <stdio.h>
#include <string.h>
struct in { int a, b; };
struct s1 { long d[3]; long n; };
struct s2 { struct in p; struct in q; char name[4]; };
struct s3 { struct in arr[2]; int tail; };
union u { long d[2]; int i; };
struct s4 { union u x; int y; };
static struct s1 g1 = {1, 2, 3, 4};
static struct s2 g2 = {5, 6, 7, 8, "abc"};
static long g3[2][3] = {1, 2, 3, 4};
static struct s3 g4 = {1, 2, 3, 4, 5};
static struct s1 g5 = {0};
int main(void) {
    struct in src = {9, 10};
    struct s1 a = {1, 2, 3, 4};
    struct s1 b = {7};
    struct s2 c = {5, 6, 7, 8, "abc"};
    struct s2 d = {src, {11, 12}, "xy"};
    long e[2][3] = {1, 2, 3, 4};
    long f[2][3] = {{1}, 2, 3, 4};
    struct s3 g = {1, 2, 3, 4, 5};
    struct s3 h = {{1, 2}, 3, 4, 5};
    struct s4 k = {1, 2};
    struct s4 m = {{3, 4}, 5};
    struct s1 arr[2] = {1, 2, 3, 4, 5};
    printf("%ld %ld %ld %ld\n", a.d[0], a.d[2], a.n, b.n + b.d[0] + b.d[2]);
    printf("%d %d %d %d %s | %d %d %d %d %s\n", c.p.a, c.p.b, c.q.a, c.q.b, c.name, d.p.a, d.p.b, d.q.a, d.q.b, d.name);
    printf("%ld %ld %ld %ld %ld\n", e[0][2], e[1][0], e[1][1], f[0][1], f[1][0]);
    printf("%d %d %d %d %d | %d %d %d %d %d\n", g.arr[0].a, g.arr[0].b, g.arr[1].a, g.arr[1].b, g.tail, h.arr[0].a, h.arr[0].b, h.arr[1].a, h.arr[1].b, h.tail);
    printf("%ld %d | %ld %d | %ld %ld %ld\n", k.x.d[0], k.y, m.x.d[0], m.y, arr[0].n, arr[1].d[0], arr[1].d[1]);
    printf("%ld %ld %d %s %ld %d %ld\n", g1.d[2], g1.n, g2.q.a, g2.name, g3[1][0], g4.arr[1].b + g4.tail, g5.n);
    return 0;
}
