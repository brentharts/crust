#include <stdio.h>
#include <stdint.h>
int64_t goose_double(int64_t x);
int64_t sum_slice(int64_t *p, int64_t n);
int64_t divmod(int64_t a, int64_t b, int64_t *rem);
int64_t bump(void);
double scale(double x, double k);

int main(void) {
    int64_t xs[4] = {1, 2, 3, 4}, r;
    int64_t q = divmod(17, 5, &r);
    bump();
    printf("%ld %ld %ld %ld %ld %.1f\n", (long)goose_double(21), (long)sum_slice(xs, 4),
           (long)q, (long)r, (long)bump(), scale(1.5, 4.0));
    return 0;
}
