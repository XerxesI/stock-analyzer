"""Post-commit integrity audit for EXP-005's admission/reservation/order facts
(Revision 5, Section 8.1). Reports structured findings only -- never repairs,
deletes, or manufactures records. Intended to be run against a single, isolated
EXP-005 database (every entry_orders row in such a database was created via
AdmissionTransactionService's atomic path -- this assumption does not hold against a
general-purpose sandbox database, e.g. EXP-004's, which never populates
portfolio_admissions at all and would falsely report every order as orphaned).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

ADMISSION_WITHOUT_RESERVATION = "ADMISSION_WITHOUT_RESERVATION"
ADMISSION_WITHOUT_ORDER = "ADMISSION_WITHOUT_ORDER"
RESERVATION_WITHOUT_ADMISSION = "RESERVATION_WITHOUT_ADMISSION"
MULTIPLE_RESERVATIONS_FOR_ADMISSION = "MULTIPLE_RESERVATIONS_FOR_ADMISSION"
ORDER_WITHOUT_ADMISSION = "ORDER_WITHOUT_ADMISSION"
NO_CAPACITY_WITH_RESERVATION = "NO_CAPACITY_WITH_RESERVATION"


@dataclass(frozen=True)
class OrphanFinding:
    category: str
    admission_id: str | None
    detail: str


def check_admission_integrity(conn: sqlite3.Connection, replay_id: str) -> list[OrphanFinding]:
    findings: list[OrphanFinding] = []

    for row in conn.execute(
        "SELECT pa.admission_id FROM portfolio_admissions pa "
        "LEFT JOIN slot_reservations sr ON sr.admission_id = pa.admission_id "
        "WHERE pa.replay_id = ? AND pa.decision = 'ACCEPTED' AND sr.reservation_id IS NULL "
        "ORDER BY pa.admission_id",
        (replay_id,),
    ).fetchall():
        findings.append(
            OrphanFinding(ADMISSION_WITHOUT_RESERVATION, row["admission_id"], "ACCEPTED admission has no slot_reservations row")
        )

    for row in conn.execute(
        "SELECT pa.admission_id FROM portfolio_admissions pa "
        "LEFT JOIN entry_orders eo ON eo.candidate_id = pa.admission_id "
        "WHERE pa.replay_id = ? AND pa.decision = 'ACCEPTED' AND eo.order_id IS NULL "
        "ORDER BY pa.admission_id",
        (replay_id,),
    ).fetchall():
        findings.append(
            OrphanFinding(ADMISSION_WITHOUT_ORDER, row["admission_id"], "ACCEPTED admission has no entry_orders row")
        )

    for row in conn.execute(
        "SELECT sr.reservation_id, sr.admission_id FROM slot_reservations sr "
        "LEFT JOIN portfolio_admissions pa ON pa.admission_id = sr.admission_id "
        "WHERE sr.replay_id = ? AND pa.admission_id IS NULL "
        "ORDER BY sr.reservation_id",
        (replay_id,),
    ).fetchall():
        findings.append(
            OrphanFinding(
                RESERVATION_WITHOUT_ADMISSION,
                row["admission_id"],
                f"reservation {row['reservation_id']} has no owning portfolio_admissions row",
            )
        )

    for row in conn.execute(
        "SELECT admission_id, COUNT(*) AS n FROM slot_reservations WHERE replay_id = ? "
        "GROUP BY admission_id HAVING COUNT(*) > 1 ORDER BY admission_id",
        (replay_id,),
    ).fetchall():
        findings.append(
            OrphanFinding(MULTIPLE_RESERVATIONS_FOR_ADMISSION, row["admission_id"], f"{row['n']} reservations found")
        )

    for row in conn.execute(
        "SELECT eo.order_id, eo.candidate_id FROM entry_orders eo "
        "LEFT JOIN portfolio_admissions pa ON pa.admission_id = eo.candidate_id "
        "WHERE pa.admission_id IS NULL "
        "ORDER BY eo.order_id"
    ).fetchall():
        findings.append(
            OrphanFinding(
                ORDER_WITHOUT_ADMISSION, row["candidate_id"], f"entry_orders {row['order_id']} references no portfolio_admissions row"
            )
        )

    for row in conn.execute(
        "SELECT pa.admission_id FROM portfolio_admissions pa "
        "JOIN slot_reservations sr ON sr.admission_id = pa.admission_id "
        "WHERE pa.replay_id = ? AND pa.decision = 'NO_CAPACITY' "
        "ORDER BY pa.admission_id",
        (replay_id,),
    ).fetchall():
        findings.append(
            OrphanFinding(NO_CAPACITY_WITH_RESERVATION, row["admission_id"], "NO_CAPACITY admission has a slot_reservations row")
        )

    return findings
