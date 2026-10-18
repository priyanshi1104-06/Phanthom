# Each method has one bug that a boundary input finds, except leap_year?: its bug is a wrong
# answer, not a crash, so finding it needs an intent check (the Analyst layer), not Specter.

MONTHS = %w[jan feb mar apr may jun jul aug sep oct nov dec].each_with_index.to_h { |m, i| [m, i + 1] }.freeze

def average(values)
  values.sum / values.size
end

def median(values)
  sorted = values.sort
  sorted.fetch(sorted.size / 2)
end

def first_word(text)
  text.split.first.capitalize
end

def month_number(name)
  MONTHS.fetch(name[0, 3].downcase)
end

def badge(count)
  "You have " + count + " new messages"
end

def percent(score, total)
  (score * 100.0 / total).round
end

def chunks(items, size)
  items.each_slice(size).to_a
end

def collatz_steps(n)
  steps = 0
  until n == 1
    n = n.even? ? n / 2 : 3 * n + 1
    steps += 1
  end
  steps
end

def digit_sum(n)
  n.zero? ? 0 : n % 10 + digit_sum(n / 10)
end

def leap_year?(year)
  (year.to_i % 4).zero?
end

puts average([1, 2, 3])
