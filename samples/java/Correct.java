import java.util.Map;

// Correct methods: Specter must report nothing here (false-positive check).
public class Correct {
    public static double safeAverage(int[] values) {
        if (values == null || values.length == 0) {
            return 0.0;
        }
        long sum = 0;
        for (int value : values) {
            sum += value;
        }
        return (double) sum / values.length;
    }

    public static int wordCount(String text) {
        if (text == null || text.isBlank()) {
            return 0;
        }
        return text.trim().split("\\s+").length;
    }

    public static int clamp(int x, int low, int high) {
        return Math.max(low, Math.min(x, high));
    }

    public static int stockLeft(Map<String, Integer> inventory, String item) {
        if (inventory == null) {
            return 0;
        }
        return Math.max(inventory.getOrDefault(item, 0), 0);
    }
}
