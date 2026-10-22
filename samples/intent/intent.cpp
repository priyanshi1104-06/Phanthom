// Functions that run fine and answer wrong, with examples in comments. Analyst catches the wrong ones.
#include <algorithm>

// True for leap years in the Gregorian calendar: is_leap_year(1900) == false
bool is_leap_year(int year) {
    return year % 4 == 0;
}

// Limit value to the range low..high: clamp(15, 0, 10) == 10
int clamp(int value, int low, int high) {
    return std::max(low, std::min(value, low));
}

// Celsius to Fahrenheit: to_fahrenheit(100.0) == 212.0
double to_fahrenheit(double celsius) {
    return celsius * 9 / 5 + 23;
}

// The larger of two numbers: max_of(3, 7) == 7
int max_of(int a, int b) {
    return a > b ? a : b;
}
