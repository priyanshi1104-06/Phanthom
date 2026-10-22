// Each function has one bug that a boundary input finds, except is_leap_year: its bug is a wrong
// answer, not a crash, so finding it needs an intent check (the Analyst layer), not Specter.
#include <algorithm>
#include <cstddef>
#include <map>
#include <string>
#include <vector>

int average(const std::vector<int>& values) {
    int total = 0;
    for (int value : values) total += value;
    return total / static_cast<int>(values.size());
}

int top_score(const std::vector<int>& scores) {
    return *std::max_element(scores.begin(), scores.end());
}

int port_of(const std::string& address) {
    std::size_t colon = address.find(':');
    return std::stoi(address.substr(colon + 1));
}

char last_char(const std::string& text) {
    return text[text.size() - 1];
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
    if (n == 0) return 1;
    return n * factorial(n - 1);
}

int price_of(const std::string& item) {
    static const std::map<std::string, int> prices = {{"apple", 50}, {"pear", 75}};
    return prices.at(item);
}

unsigned checksum(const unsigned char* data, std::size_t length) {
    unsigned sum = 0;
    for (std::size_t i = 0; i <= length; i++) sum += data[i];
    return sum;
}

int midpoint(int low, int high) {
    return (low + high) / 2;
}

bool is_leap_year(int year) {
    return year % 4 == 0;
}

int main() {
    return average({1, 2, 3}) == 2 ? 0 : 1;
}
