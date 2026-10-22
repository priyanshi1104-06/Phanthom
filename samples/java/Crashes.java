package samples;

import java.util.Collections;
import java.util.List;
import java.util.Map;

// Each static method has one bug that a boundary input finds, except isLeapYear: its bug is a wrong
// answer, not a crash, so finding it needs an intent check (the Analyst layer), not Specter.
public class Crashes {
    public static int average(int[] values) {
        int sum = 0;
        for (int value : values) {
            sum += value;
        }
        return sum / values.length;
    }

    public static int[] pairwiseDiffs(int[] values) {
        int[] diffs = new int[values.length];
        for (int i = 0; i < values.length; i++) {
            diffs[i] = values[i + 1] - values[i];
        }
        return diffs;
    }

    public static String initials(String fullName) {
        StringBuilder out = new StringBuilder();
        for (String part : fullName.split(" ")) {
            out.append(Character.toUpperCase(part.charAt(0)));
        }
        return out.toString();
    }

    public static int parsePort(String address) {
        String port = address.substring(address.indexOf(':') + 1);
        return Integer.parseInt(port);
    }

    public static int stockLeft(Map<String, Integer> inventory, String item) {
        int count = inventory.get(item);
        return Math.max(count, 0);
    }

    public static int spread(List<Integer> readings) {
        return Collections.max(readings) - Collections.min(readings);
    }

    public static int collatzSteps(int n) {
        int steps = 0;
        while (n != 1) {
            n = n % 2 == 0 ? n / 2 : 3 * n + 1;
            steps++;
        }
        return steps;
    }

    public static long factorial(int n) {
        if (n == 0) {
            return 1;
        }
        return n * factorial(n - 1);
    }

    private static int percent(int part, int whole) {
        return part * 100 / whole;
    }

    public static boolean isLeapYear(int year) {
        return year % 4 == 0;
    }

    public static void main(String[] args) {
        System.out.println(average(new int[]{1, 2, 3}) + " " + percent(1, 4));
    }
}
