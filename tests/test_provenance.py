# Copyright (c) 2026 FarmCheck contributors.
"""Unknown unit bases remain unknown in both detail and group presentation."""

from pathlib import Path

from farmcheck.contracts import Dimensions
from farmcheck.service import Service
from farmcheck.source import Catalog, inventory, make_record
from farmcheck.store import Store


def test_unknown_peer_units_are_not_promoted_to_source_declared(tmp_path: Path) -> None:
    """Two compatible unknown bases can group without inventing declared units."""
    raw = """{
      "images":{"class_name":"녹차","photo_date":"240803122500"},
      "environment":{"ph":"16","ec":"1","co2":"400","temp":"25","humidity":"70",
                     "env_date":"2024-08-03 12:25:00"},
      "growth":{"growth_cycle":"3"}
    }"""
    first = make_record(raw, "synthetic/unknown-a.json", Dimensions(width=380, height=380))
    second = make_record(raw, "synthetic/unknown-b.json", Dimensions(width=380, height=380))
    records = {first.id: first, second.id: second}
    catalog = Catalog(records, inventory(records, (2, 2), None))
    store = Store(tmp_path / "unknown.sqlite")
    service = Service(catalog, store, "synthetic")
    workspace = store.workspace(None)
    detail = service.detail(first.id, store.read(workspace)).root
    peers = detail["peers"]
    assert isinstance(peers, list)
    assert len(peers) == 1
    peer = peers[0]
    assert isinstance(peer, dict)
    assert peer["unit_status"] == "unknown"
    group = detail["group"]
    assert isinstance(group, dict)
    key = group["key"]
    assert isinstance(key, dict)
    units = key["units"]
    assert isinstance(units, dict)
    ec = units["environment.ec"]
    assert isinstance(ec, dict)
    assert ec["status"] == "unknown"
    assert ec["raw_unit"] is None
