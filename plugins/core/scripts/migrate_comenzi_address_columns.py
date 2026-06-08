"""One-off migration: split `Adresa` into Adresa / Oraș / Județ / Cod poștal.

Before: A Site, B Luna, C Nume, D Link, E Arome, F Adresa, G Tel, H Status,
        I Mesaj, J Status colet, K Content
After:  A..E unchanged
        F Adresa (street + bloc/scara/etaj/ap only)
        G Oraș            (NEW)
        H Județ           (NEW)
        I Cod poștal      (NEW)
        J Tel             (was G)
        K Status          (was H)
        L Mesaj status    (was I)
        M Status colet    (was J)
        N Content         (was K)

Idempotent: aborts if header row already has Oraș in G1.
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.place_ugc_order import MS_BASE, ms_headers  # noqa: E402
from scripts.ro_address import parse_address  # noqa: E402


def _street_only(addr_dict: dict) -> str:
    """Combine address1 + address2 → single 'street, apt' string for col F."""
    p1 = (addr_dict.get("address1") or "").strip()
    p2 = (addr_dict.get("address2") or "").strip()
    if p1 and p2:
        return f"{p1}, {p2}"
    return p1 or p2


def main() -> None:
    h = ms_headers()
    hj = {**h, "Content-Type": "application/json"}

    # Snapshot
    r = requests.get(f"{MS_BASE}/worksheets('Comenzi')/usedRange", headers=h, timeout=30)
    r.raise_for_status()
    used = r.json()
    rows = used["values"]
    n_rows = used["rowCount"]
    n_cols = used["columnCount"]
    print(f"current: {used['address']} ({n_rows} rows × {n_cols} cols)")
    print(f"header: {rows[0]}")

    if rows[0][6] == "Oraș" or (n_cols >= 14 and rows[0][6] in {"Oraș", "Oras"}):
        print("Already migrated (G1 = Oraș). Aborting.")
        return

    # Pre-compute new F/G/H/I per data row.
    new_fghi: list[list[str]] = []
    for i, row in enumerate(rows[1:], start=2):
        raw_addr = (row[5] or "").strip() if len(row) > 5 else ""
        if not raw_addr:
            new_fghi.append(["", "", "", ""])
            continue
        parsed = parse_address(raw_addr)
        new_fghi.append([
            _street_only(parsed),
            parsed.get("city") or "",
            parsed.get("province") or "",
            parsed.get("zip") or "",
        ])
        print(f"row {i:>2}: {raw_addr!r}")
        print(f"      → F={new_fghi[-1][0]!r}")
        print(f"        G={new_fghi[-1][1]!r}  H={new_fghi[-1][2]!r}  I={new_fghi[-1][3]!r}")

    # Step 1: insert 3 columns at G:I (shift right).
    print("\nInserting 3 columns at G:I (shift right)…")
    r = requests.post(
        f"{MS_BASE}/worksheets('Comenzi')/range(address='G:I')/insert",
        headers=hj, json={"shift": "Right"}, timeout=30,
    )
    r.raise_for_status()
    print("  inserted.")

    # Step 2: write new headers G1:I1.
    print("Writing new headers…")
    r = requests.patch(
        f"{MS_BASE}/worksheets('Comenzi')/range(address='G1:I1')",
        headers=hj, json={"values": [["Oraș", "Județ", "Cod poștal"]]},
        timeout=30,
    )
    r.raise_for_status()

    # Step 3: write data rows F2:I{N} in one shot.
    last = n_rows  # rowCount stays the same (we shifted, didn't add rows)
    print(f"Writing F2:I{last}…")
    r = requests.patch(
        f"{MS_BASE}/worksheets('Comenzi')/range(address='F2:I{last}')",
        headers=hj, json={"values": new_fghi}, timeout=60,
    )
    r.raise_for_status()
    print("done.")


if __name__ == "__main__":
    main()
