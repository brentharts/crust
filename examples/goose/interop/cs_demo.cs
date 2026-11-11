public class Report
{
    public static long Longest(long upto)
    {
        long best = 1;
        long bestSteps = 0;
        for (long n = 1; n <= upto; n++)
        {
            long s = collatz_steps(n);
            count_call();
            if (s > bestSteps) { bestSteps = s; best = n; }
        }
        return best * 1000 + bestSteps;
    }
}
