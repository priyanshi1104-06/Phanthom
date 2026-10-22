# Functions that run fine and answer wrong, with examples in comments. Analyst catches the wrong ones.

# True for leap years in the Gregorian calendar.
#   is_leap_year(1900) # => false
def is_leap_year(year)
  year % 4 == 0
end

# Limit value to the range low..high.
#   clamp(15, 0, 10) # => 10
def clamp(value, low, high)
  [[value, low].min, low].max
end

# Celsius to Fahrenheit.
#   to_fahrenheit(100.0) # => 212.0
def to_fahrenheit(celsius)
  celsius * 9 / 5 + 23
end

# The larger of two numbers.
#   max_of(3, 7) # => 7
def max_of(a, b)
  a > b ? a : b
end
