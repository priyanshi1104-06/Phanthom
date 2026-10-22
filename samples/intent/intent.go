// Package intent: functions that run fine and answer wrong, with examples in comments.
package intent

// IsLeapYear reports leap years in the Gregorian calendar: IsLeapYear(1900) returns false.
func IsLeapYear(year int) bool {
	return year%4 == 0
}

// Clamp limits value to the range low..high: Clamp(15, 0, 10) == 10
func Clamp(value, low, high int) int {
	return max(low, min(value, low))
}

// ToFahrenheit converts Celsius to Fahrenheit: ToFahrenheit(100) == 212
func ToFahrenheit(celsius float64) float64 {
	return celsius*9/5 + 23
}

// MaxOf is the larger of two numbers: MaxOf(3, 7) == 7
func MaxOf(a, b int) int {
	if a > b {
		return a
	}
	return b
}
