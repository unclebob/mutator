package demo

class Calc {
    fun sign(x: Int): Int {
        if (x > 0) return 1
        if (x < 0) return -1
        return 0
    }

    fun below(x: Int, lo: Int): Boolean = x < lo

    fun zero(v: Int): Boolean = v == 0

    fun only(a: Boolean, b: Boolean): Boolean = a && !b

    fun orZero(n: Int?): Int = n ?: 0

    fun len(s: String?): Int? = s?.length
}
