REQUESTED = "requested"
STATUSES = frozenset({"requested", "accepted", "in_progress", "resolved", "closed", "cancelled"})

TRANSITIONS = {
    ("owner", "requested"): frozenset({"cancelled"}),
    ("owner", "resolved"): frozenset({"closed"}),
    ("technician", "requested"): frozenset({"accepted"}),
    ("technician", "accepted"): frozenset({"in_progress"}),
    ("technician", "in_progress"): frozenset({"resolved"}),
}


def can_transition(role, previous, next_status):
    return next_status in TRANSITIONS.get((role, previous), ())
