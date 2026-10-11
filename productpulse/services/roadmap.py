"""Roadmap builder: horizon placement, ordering, snapshots and version comparison."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from productpulse.db.models import Opportunity, RoadmapItem, RoadmapSnapshot, utcnow
from productpulse.services import audit
from productpulse.services.prioritization import score_of

DEFAULT_HORIZONS = ("Now", "Next", "Later")
ITEM_STATUSES = ("Planned", "In progress", "Blocked", "Done", "Deferred")


class RoadmapError(ValueError):
    pass


def horizons(session: Session) -> list[str]:
    used = [h for h in session.scalars(select(RoadmapItem.horizon).distinct()) if h]
    return list(DEFAULT_HORIZONS) + sorted(h for h in used if h not in DEFAULT_HORIZONS)


def items(session: Session) -> list[RoadmapItem]:
    order = {h: i for i, h in enumerate(horizons(session))}
    rows = list(session.scalars(select(RoadmapItem)))
    return sorted(rows, key=lambda r: (order.get(r.horizon, 99), r.position, r.id))


def _clean_horizon(h: str) -> str:
    h = " ".join((h or "").split())
    if not 1 <= len(h) <= 30:
        raise RoadmapError("Horizon names must be 1–30 characters.")
    return h


def _next_position(session: Session, horizon: str) -> int:
    current_max = session.scalar(
        select(func.coalesce(func.max(RoadmapItem.position), -1)).where(RoadmapItem.horizon == horizon)
    )
    return int(current_max) + 1  # note: max position 0 is valid, so no `or` fallback


def add_to_roadmap(session: Session, opportunity_ids: list[int], horizon: str = "Next") -> list[RoadmapItem]:
    horizon = _clean_horizon(horizon)
    added = []
    for oid in opportunity_ids:
        opp = session.get(Opportunity, oid)
        if opp is None:
            raise RoadmapError(f"Opportunity {oid} not found.")
        if session.scalar(select(RoadmapItem).where(RoadmapItem.opportunity_id == oid)):
            continue  # already on the roadmap
        item = RoadmapItem(
            opportunity_id=oid,
            horizon=horizon,
            position=_next_position(session, horizon),
            owner=opp.owner,
            rationale=opp.business_rationale[:2000],
        )
        session.add(item)
        session.flush()
        if opp.status in ("Proposed", "Under review"):
            opp.status = "Planned"
        audit.record(session, "roadmap_added", "roadmap_item", item.id, f"Added “{opp.name}” to {horizon}.")
        added.append(item)
    return added


def update_item(
    session: Session,
    item_id: int,
    *,
    horizon: str | None = None,
    owner: str | None = None,
    status: str | None = None,
    target_date: date | datetime | None = None,
    dependencies: str | None = None,
    rationale: str | None = None,
    clear_target_date: bool = False,
) -> RoadmapItem:
    item = session.get(RoadmapItem, item_id)
    if item is None:
        raise RoadmapError("Roadmap item not found.")
    changes = []
    if horizon is not None:
        horizon = _clean_horizon(horizon)
        if horizon != item.horizon:
            changes.append(f"horizon {item.horizon} → {horizon}")
            item.horizon, item.position = horizon, _next_position(session, horizon)
    if status is not None and status != item.status:
        if status not in ITEM_STATUSES:
            raise RoadmapError(f"Status must be one of {ITEM_STATUSES}.")
        changes.append(f"status {item.status} → {status}")
        item.status = status
    if owner is not None and owner.strip() != item.owner:
        changes.append(f"owner → {owner.strip() or '—'}")
        item.owner = owner.strip()[:100]
    if clear_target_date and item.target_date is not None:
        changes.append("target date cleared")
        item.target_date = None
    elif target_date is not None:
        td = target_date if isinstance(target_date, datetime) else datetime.combine(target_date, datetime.min.time())
        if td != item.target_date:
            changes.append(f"target date → {td.date().isoformat()}")
            item.target_date = td
    if dependencies is not None and dependencies != item.dependencies:
        changes.append("dependencies edited")
        item.dependencies = dependencies[:2000]
    if rationale is not None and rationale != item.rationale:
        changes.append("rationale edited")
        item.rationale = rationale[:5000]
    if changes:
        item.updated_at = utcnow()
        audit.record(
            session,
            "roadmap_updated",
            "roadmap_item",
            item.id,
            f"Roadmap “{item.opportunity.name}”: " + "; ".join(changes),
        )
    return item


def move(session: Session, item_id: int, direction: int) -> None:
    """Swap with the neighbour above (direction=-1) or below (+1) in the same horizon."""
    item = session.get(RoadmapItem, item_id)
    if item is None:
        raise RoadmapError("Roadmap item not found.")
    peers = [i for i in items(session) if i.horizon == item.horizon]
    idx = peers.index(item)
    j = idx + direction
    if not 0 <= j < len(peers):
        return
    for pos, it in enumerate(peers):  # normalise positions first
        it.position = pos
    peers[idx].position, peers[j].position = peers[j].position, peers[idx].position
    audit.record(
        session,
        "roadmap_reordered",
        "roadmap_item",
        item.id,
        f"Moved “{item.opportunity.name}” {'up' if direction < 0 else 'down'} in {item.horizon}.",
    )


def remove(session: Session, item_id: int) -> None:
    item = session.get(RoadmapItem, item_id)
    if item:
        audit.record(
            session, "roadmap_removed", "roadmap_item", item.id, f"Removed “{item.opportunity.name}” from the roadmap."
        )
        session.delete(item)


def board_frame(session: Session) -> pd.DataFrame:
    rows = []
    for it in items(session):
        o = it.opportunity
        rows.append(
            {
                "item_id": it.id,
                "opportunity_id": o.id,
                "Horizon": it.horizon,
                "Order": it.position + 1,
                "Initiative": o.name,
                "RICE": score_of(o),
                "Status": it.status,
                "Owner": it.owner,
                "Target date": it.target_date.date() if it.target_date else None,
                "Dependencies": it.dependencies,
                "Rationale": it.rationale,
                "Evidence": len(o.evidence),
                "Reach is proxy": o.reach_is_proxy,
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------- snapshots


def save_snapshot(session: Session, name: str, note: str = "") -> RoadmapSnapshot:
    name = " ".join(name.split())
    if not 2 <= len(name) <= 120:
        raise RoadmapError("Version names must be 2–120 characters.")
    data = []
    for it in items(session):
        data.append(
            {
                "opportunity_id": it.opportunity_id,
                "name": it.opportunity.name,
                "horizon": it.horizon,
                "position": it.position,
                "status": it.status,
                "owner": it.owner,
                "target_date": it.target_date.date().isoformat() if it.target_date else None,
                "rice": score_of(it.opportunity),
            }
        )
    snap = RoadmapSnapshot(name=name, note=note[:2000], items=data)
    session.add(snap)
    session.flush()
    audit.record(
        session,
        "roadmap_snapshot",
        "roadmap_snapshot",
        snap.id,
        f"Saved roadmap version “{name}” ({len(data)} initiatives).",
    )
    return snap


def list_snapshots(session: Session) -> list[RoadmapSnapshot]:
    return list(
        session.scalars(select(RoadmapSnapshot).order_by(RoadmapSnapshot.created_at.desc(), RoadmapSnapshot.id.desc()))
    )


def current_as_snapshot_items(session: Session) -> list[dict]:
    return [
        {
            "opportunity_id": it.opportunity_id,
            "name": it.opportunity.name,
            "horizon": it.horizon,
            "position": it.position,
            "status": it.status,
            "owner": it.owner,
            "target_date": it.target_date.date().isoformat() if it.target_date else None,
            "rice": score_of(it.opportunity),
        }
        for it in items(session)
    ]


def compare(old: list[dict], new: list[dict]) -> pd.DataFrame:
    """Diff two roadmap versions by opportunity."""

    def rank_in_horizon(rows: list[dict]) -> dict[int, int]:
        out = {}
        for h in {r["horizon"] for r in rows}:
            for i, r in enumerate(sorted((r for r in rows if r["horizon"] == h), key=lambda r: r["position"])):
                out[r["opportunity_id"]] = i + 1
        return out

    a, b = {r["opportunity_id"]: r for r in old}, {r["opportunity_id"]: r for r in new}
    ra, rb = rank_in_horizon(old), rank_in_horizon(new)
    changes = []
    for oid in sorted(set(a) | set(b)):
        x, y = a.get(oid), b.get(oid)
        name = (y or x)["name"]
        if x is None:
            changes.append({"Initiative": name, "Change": "Added", "Detail": f"→ {y['horizon']} #{rb[oid]}"})
            continue
        if y is None:
            changes.append({"Initiative": name, "Change": "Removed", "Detail": f"was {x['horizon']} #{ra[oid]}"})
            continue
        if x["horizon"] != y["horizon"]:
            changes.append(
                {"Initiative": name, "Change": "Moved horizon", "Detail": f"{x['horizon']} → {y['horizon']}"}
            )
        elif ra[oid] != rb[oid]:
            changes.append(
                {"Initiative": name, "Change": "Reordered", "Detail": f"{x['horizon']} #{ra[oid]} → #{rb[oid]}"}
            )
        if x["status"] != y["status"]:
            changes.append({"Initiative": name, "Change": "Status", "Detail": f"{x['status']} → {y['status']}"})
        if x.get("target_date") != y.get("target_date"):
            changes.append(
                {
                    "Initiative": name,
                    "Change": "Target date",
                    "Detail": f"{x.get('target_date') or '—'} → {y.get('target_date') or '—'}",
                }
            )
        if round(x.get("rice", 0), 2) != round(y.get("rice", 0), 2):
            changes.append(
                {
                    "Initiative": name,
                    "Change": "RICE score",
                    "Detail": f"{x.get('rice', 0):,.2f} → {y.get('rice', 0):,.2f}",
                }
            )
    return pd.DataFrame(changes, columns=["Initiative", "Change", "Detail"])


def apply_layout(session: Session, layout: dict[str, list[int]]) -> int:
    """Persist a drag-and-drop board: ``{horizon: [item_id, ...]}`` in display order.

    Returns the number of items whose horizon or position changed.
    """
    by_id = {it.id: it for it in session.scalars(select(RoadmapItem))}
    seen: set[int] = set()
    changed, moves = 0, []
    for horizon, ids in layout.items():
        horizon = _clean_horizon(horizon)
        for pos, item_id in enumerate(ids):
            it = by_id.get(item_id)
            if it is None or item_id in seen:
                raise RoadmapError("The board is out of date — please refresh the page.")
            seen.add(item_id)
            if it.horizon != horizon or it.position != pos:
                if it.horizon != horizon:
                    moves.append(f"“{it.opportunity.name}” {it.horizon} → {horizon}")
                it.horizon, it.position = horizon, pos
                it.updated_at = utcnow()
                changed += 1
    if seen != set(by_id):
        raise RoadmapError("The board is out of date — please refresh the page.")
    if changed:
        audit.record(
            session,
            "roadmap_reordered",
            "roadmap",
            "",
            "Roadmap rearranged by drag and drop" + (": " + "; ".join(moves) if moves else " (order changed)."),
            {"layout": layout},
        )
    return changed
