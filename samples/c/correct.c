/* Correct functions: Specter must report nothing here, not even for NULL. */
#include <ctype.h>
#include <stddef.h>

double safe_average(const int *values, int count) {
    if (values == NULL || count <= 0)
        return 0.0;
    long long total = 0;
    for (int i = 0; i < count; i++)
        total += values[i];
    return (double)total / count;
}

size_t word_count(const char *text) {
    size_t words = 0;
    int in_word = 0;
    for (; text != NULL && *text; text++) {
        if (isspace((unsigned char)*text))
            in_word = 0;
        else if (!in_word) {
            in_word = 1;
            words++;
        }
    }
    return words;
}

int clamp(int x, int low, int high) {
    return x < low ? low : x > high ? high : x;
}

unsigned digit_sum(unsigned n) {
    unsigned sum = 0;
    while (n > 0) {
        sum += n % 10;
        n /= 10;
    }
    return sum;
}
