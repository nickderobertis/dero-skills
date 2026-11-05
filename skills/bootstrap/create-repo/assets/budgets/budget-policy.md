## Budget reports

Approved budget thresholds alone decide failures: the required `budgets` check goes
red only on an ordinary budget verdict, and the budget stays the requirement. CI
artifacts retain the base branch's recent measurements for informational comparison,
and each report states their provenance, retention and coverage. A stored measurement
is never a line to hold.

1. When a performance problem appears that a failing budget check does not already identify (for example a CI timeout), read the PR report first through `onevcs change comments` or the host PR view. If the failing check already identifies it, skip the duplicate read. Read earlier PR comments and job results when several changes may contribute.
2. Never time base versus branch on a shared host. Profile to investigate, and use CI comparisons through the repository's existing publication path.
3. When reading a report or seeing a failing budget check, fix the regression as part of the work. For an adverse change of at least one standard deviation shown in a report being read (a `max` budget rising, a `min` budget falling), make the change cheaper or make room in existing code, profiling to find where. Improvements show numbers without flags and are no reason to optimize further, and other figures need no work. Informational flags do not fail a check or start other work.
4. Stay inside existing budgets. Make room for new tests and code by speeding up existing work; never raise limits or timeouts.
5. Suspect the current change first when something degrades, before intermittent slowness or the outside world.
