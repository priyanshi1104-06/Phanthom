// Correct functions: Specter must report nothing here.
#include <cctype>
#include <cstddef>
#include <string>
#include <vector>

double safe_average(const std::vector<int>& values) {
    if (values.empty()) return 0.0;
    long long total = 0;
    for (int value : values) total += value;
    return static_cast<double>(total) / values.size();
}

std::size_t word_count(const std::string& text) {
    std::size_t words = 0;
    bool in_word = false;
    for (char c : text) {
        if (std::isspace(static_cast<unsigned char>(c))) in_word = false;
        else if (!in_word) { in_word = true; words++; }
    }
    return words;
}

int clamp(int x, int low, int high) {
    return x < low ? low : x > high ? high : x;
}

std::string shout(std::string text) {
    for (char& c : text) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    return text + "!";
}
