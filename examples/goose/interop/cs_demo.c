/* A C# class calling Goose exports: both lowered into this one C file. */
#include <stdio.h>
#include "cs_demo.goose"
#include "cs_demo.cs"
int main(void) {
    printf("%ld %ld\n", (long)Report_Longest(30), (long)count_call());
    return 0;
}
