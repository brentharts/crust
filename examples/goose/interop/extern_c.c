#include <stdint.h>
int64_t c_triple(int64_t x) { return x * 3; }
int64_t c_sum(int64_t *p, int64_t n) { int64_t t = 0; for (int64_t i = 0; i < n; i++) t += p[i]; return t; }
double scale_it(double x, double k) { return x * k; }
void c_fill(int64_t *out, int64_t v) { *out = v; }
