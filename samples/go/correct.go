// Correct functions: Specter must report nothing here. Lookup returns an error for a missing key:
// that is Go's normal error handling, not a crash.
package samples

import (
	"fmt"
	"strings"
)

func SafeAverage(values []int) int {
	if len(values) == 0 {
		return 0
	}
	total := 0
	for _, value := range values {
		total += value
	}
	return total / len(values)
}

func WordCount(text string) int {
	return len(strings.Fields(text))
}

func Clamp(x, low, high int) int {
	return max(low, min(x, high))
}

func Lookup(prices map[string]int, item string) (int, error) {
	price, ok := prices[item]
	if !ok {
		return 0, fmt.Errorf("no price for %q", item)
	}
	return price, nil
}
