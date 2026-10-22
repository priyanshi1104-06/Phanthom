// Package samples: each function has one bug that a boundary input finds, except IsLeapYear: its
// bug is a wrong answer, not a crash, so finding it needs an intent check (the Analyst layer).
package samples

import (
	"strconv"
	"strings"
)

type Account struct {
	Owner   string
	Balance int
}

func Average(values []int) int {
	total := 0
	for _, value := range values {
		total += value
	}
	return total / len(values)
}

func FirstWord(text string) string {
	word := strings.Fields(text)[0]
	return strings.ToUpper(word[:1]) + word[1:]
}

func LastPrice(prices []float64) string {
	return strconv.FormatFloat(prices[len(prices)-1], 'f', 2, 64)
}

func ParsePort(address string) (int, error) {
	port := strings.Split(address, ":")[1]
	return strconv.Atoi(port)
}

func DisplayName(user map[string]string) string {
	name := user["name"]
	return strings.ToUpper(name[:1]) + name[1:]
}

func PadLeft(label string, width int) string {
	return strings.Repeat(" ", width-len(label)) + label
}

func Deposit(account *Account, cents int) int {
	account.Balance += cents
	return account.Balance
}

func CollatzSteps(n int) int {
	steps := 0
	for n != 1 {
		if n%2 == 0 {
			n /= 2
		} else {
			n = 3*n + 1
		}
		steps++
	}
	return steps
}

func Factorial(n int) int {
	if n == 0 {
		return 1
	}
	return n * Factorial(n-1)
}

func IsLeapYear(year int) bool {
	return year%4 == 0
}
