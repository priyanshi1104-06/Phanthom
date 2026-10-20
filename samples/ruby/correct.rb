# Correct methods: Specter must report nothing here, not even when they are handed nil.

def safe_average(values)
  return 0.0 if values.nil? || values.empty?

  values.sum.fdiv(values.size)
end

def word_count(text)
  text.to_s.split.size
end

def initials(full_name)
  full_name.to_s.split.map { |part| part[0].upcase }.join
end

def split_bill(total_cents, people)
  return 0 if people.nil? || people <= 0

  total_cents.to_i / people
end
