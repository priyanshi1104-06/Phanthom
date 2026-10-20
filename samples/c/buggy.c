/* Each function has one bug that a boundary input finds, except is_leap_year: its bug is a wrong
   answer, not a crash, so finding it needs an intent check (the Analyst layer), not Specter. */
#include <stdlib.h>
#include <string.h>

int average(const int *values, int count) {
    int total = 0;
    for (int i = 0; i < count; i++)
        total += values[i];
    return total / count;
}

int last_element(const int *values, int count) {
    return values[count];
}

int port_of(const char *address) {
    const char *colon = strchr(address, ':');
    return atoi(colon + 1);
}

char last_char(const char *text) {
    size_t length = strlen(text);
    return text[length - 1];
}

int collatz_steps(int n) {
    int steps = 0;
    while (n != 1) {
        n = n % 2 == 0 ? n / 2 : 3 * n + 1;
        steps++;
    }
    return steps;
}

long factorial(int n) {
    if (n == 0)
        return 1;
    return n * factorial(n - 1);
}

char *join_words(const char *first, const char *second) {
    char *out = malloc(strlen(first) + strlen(second) + 1);
    strcpy(out, first);
    strcat(out, " ");
    strcat(out, second);
    return out;
}

int digit_value(char c) {
    static const int values[10] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9};
    return values[c - '0'];
}

int midpoint(int low, int high) {
    return (low + high) / 2;
}

int is_leap_year(int year) {
    return year % 4 == 0;
}

int main(void) {
    return average((int[]){1, 2, 3}, 3) == 2 ? 0 : 1;
}
