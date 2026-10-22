// Functions that run fine and answer wrong, with examples in Javadoc. Analyst catches the wrong ones.
public class Intent {
    /**
     * True for leap years in the Gregorian calendar.
     * isLeapYear(1900) == false
     */
    public static boolean isLeapYear(int year) {
        return year % 4 == 0;
    }

    /** Limit value to the range low..high: clamp(15, 0, 10) == 10 */
    public static int clamp(int value, int low, int high) {
        return Math.max(low, Math.min(value, low));
    }

    /** Celsius to Fahrenheit: toFahrenheit(100.0) == 212.0 */
    public static double toFahrenheit(double celsius) {
        return celsius * 9 / 5 + 23;
    }

    /** The larger of two numbers: maxOf(3, 7) == 7 */
    public static int maxOf(int a, int b) {
        return a > b ? a : b;
    }
}
