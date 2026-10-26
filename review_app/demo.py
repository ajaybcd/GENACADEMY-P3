"""Illustrative fixtures only; these are not real evaluation samples."""
from .domain import Snapshot, SourceFile


def demo_review():
    files = {
        "examples/orders.py": SourceFile("examples/orders.py",
            "@@ -0,0 +1,2 @@\n+def average_order(totals):\n+    return sum(totals) / len(totals)",
            "def average_order(totals):\n    return sum(totals) / len(totals)\n"),
        "examples/login.py": SourceFile("examples/login.py",
            "@@ -0,0 +1,3 @@\n+def lookup(cursor, email):\n+    query = f\"SELECT * FROM users WHERE email='{email}'\"\n+    return cursor.execute(query).fetchone()",
            "def lookup(cursor, email):\n    query = f\"SELECT * FROM users WHERE email='{email}'\"\n    return cursor.execute(query).fetchone()\n"),
    }
    findings = [
        {"path": "examples/login.py", "category": "Security", "severity": "High",
         "snippet": "    query = f\"SELECT * FROM users WHERE email='{email}'\"",
         "body": "An attacker-controlled email changes the SQL query through string interpolation. Bind email as a database parameter instead of formatting it into the statement."},
        {"path": "examples/orders.py", "category": "Correctness", "severity": "Medium",
         "snippet": "    return sum(totals) / len(totals)",
         "body": "An empty totals list raises ZeroDivisionError. Define and handle the empty-order result before dividing."},
        {"path": "examples/orders.py", "category": "Tests", "severity": "Medium",
         "snippet": "    return sum(totals) / len(totals)",
         "body": "Add a regression test calling average_order([]), asserting the agreed empty-order result. The supplied fixture contains no test for this new edge case."},
    ]
    return Snapshot("offline-demo", "Illustrative checkout changes", "demo-head", "demo-base", "", files, demo=True), findings
