"""Which jobs are flexible, and for how long may they wait (spec section 5)."""


def max_wait_for(rules, job) -> int | None:
    """Minutes the job may wait, or None if Wattshift must not touch it. First matching rule wins; a rule with
    max_wait 0 is an explicit exclusion. `job` needs qos, partition, user, account and name attributes."""
    for r in rules:
        if all(getattr(job, k) == v for k, v in r.exact) and (r.name_re is None or r.name_re.search(job.name)):
            return r.max_wait_min or None
    return None
