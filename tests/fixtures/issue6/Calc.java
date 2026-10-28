package demo;

public class Calc {
    public int sign(int x) {
        if (x > 0) return 1;
        if (x < 0) return -1;
        return 0;
    }

    public boolean below(int x, int lo) {
        return x < lo;
    }

    public boolean zero(int v) {
        return v == 0;
    }

    public boolean only(boolean a, boolean b) {
        return a && !b;
    }

    public int orZero(Integer n) {
        return n != null ? n : 0;
    }

    public Integer len(String s) {
        return s != null ? s.length() : null;
    }
}
