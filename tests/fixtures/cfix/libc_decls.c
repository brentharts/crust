#include <stdio.h>
#include <stdlib.h>
#include <math.h>
int main(void) {
    char *e; double d = strtod("2.5x", &e); float f = strtof("1.25", 0);
    long long a = strtoll("-9000000000", 0, 10); unsigned long long b = strtoull("18446744073709551615", 0, 10);
    printf("%.1f %s %.2f %lld %llu %.1f %lld %.1f\n", d, e, f, a, b, atof("3.5"), atoll("77"), (double)fmaf(2.0f, 3.0f, 1.0f));
    printf("%.3f %.3f %ld\n", (double)exp2f(3.0f), (double)expm1f(0.0f), lroundf(2.5f));
    return 0;
}
