from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.admin_auth import require_admin_token


FLOWCITY_ROOT = Path(__file__).resolve().parents[3]
if str(FLOWCITY_ROOT) not in sys.path:
    sys.path.insert(0, str(FLOWCITY_ROOT))


router = APIRouter()


DATA_DIR = FLOWCITY_ROOT / "data"
DATASETS: dict[str, dict[str, Any]] = {
    "areas": {
        "label": "商圈区域",
        "filename": "mock_areas.json",
        "collections": ["areas"],
        "description": "商圈、地标、适合人群、基础距离和区域粗排信息。",
    },
    "activities": {
        "label": "活动 POI",
        "filename": "mock_activities.json",
        "collections": ["activities"],
        "description": "自然观察、展览、手作、运动、亲子等活动供给。",
    },
    "restaurants": {
        "label": "餐厅 POI",
        "filename": "mock_restaurants.json",
        "collections": ["restaurants"],
        "description": "餐厅、菜系、人均、氛围、行为和人群画像标签。",
    },
    "routes": {
        "label": "路线成本",
        "filename": "mock_routes.json",
        "collections": ["routes"],
        "description": "商圈之间和跨城进入西安的时间、距离、交通方式和费用。",
    },
    "availability": {
        "label": "动态供给",
        "filename": "mock_availability.json",
        "collections": ["activityAvailability", "restaurantAvailability"],
        "description": "活动余票、餐厅座位、排队、预约和动态可用性。",
    },
    "deals": {
        "label": "套餐团购",
        "filename": "mock_deals.json",
        "collections": ["deals"],
        "description": "团购套餐、价格、库存、适用人数和有效时段。",
    },
    "runtime_status": {
        "label": "执行异常池",
        "filename": "mock_runtime_status.json",
        "collections": [
            "activityRuntimeStatus",
            "restaurantRuntimeStatus",
            "routeRuntimeStatus",
            "dealRuntimeStatus",
        ],
        "description": "确认模拟执行时的无票、无座、路线拥堵等动态异常。",
    },
}


class RecordPayload(BaseModel):
    record: dict[str, Any]


def _dataset_config(slug: str) -> dict[str, Any]:
    config = DATASETS.get(slug)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Unknown dataset: {slug}")
    return config


def _dataset_path(slug: str) -> Path:
    config = _dataset_config(slug)
    path = (DATA_DIR / str(config["filename"])).resolve()
    if DATA_DIR.resolve() not in path.parents and path != DATA_DIR.resolve():
        raise HTTPException(status_code=403, detail="Invalid dataset path")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Dataset file not found: {config['filename']}")
    return path


def _read_dataset(slug: str) -> dict[str, Any]:
    try:
        return json.loads(_dataset_path(slug).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid JSON in dataset {slug}") from exc


def _write_dataset(slug: str, data: dict[str, Any]) -> None:
    path = _dataset_path(slug)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _record_fields(records: list[Any]) -> list[str]:
    fields: set[str] = set()
    for record in records:
        if isinstance(record, dict):
            fields.update(record.keys())
    return sorted(fields)


def _collection_payload(data: dict[str, Any], key: str) -> dict[str, Any]:
    records = data.get(key, [])
    if not isinstance(records, list):
        records = []
    return {
        "key": key,
        "count": len(records),
        "fields": _record_fields(records),
        "records": records,
    }


def _dataset_payload(slug: str) -> dict[str, Any]:
    config = _dataset_config(slug)
    path = _dataset_path(slug)
    data = _read_dataset(slug)
    return {
        "slug": slug,
        "label": config["label"],
        "filename": config["filename"],
        "description": config["description"],
        "version": data.get("version"),
        "city": data.get("city"),
        "dataType": data.get("dataType"),
        "note": data.get("note"),
        "updatedAt": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        "collections": [_collection_payload(data, key) for key in config["collections"]],
    }


def _collection_records(data: dict[str, Any], collection_key: str) -> list[Any]:
    records = data.get(collection_key)
    if not isinstance(records, list):
        raise HTTPException(status_code=404, detail=f"Unknown collection: {collection_key}")
    return records


@router.get("/datasets")
def list_datasets(_: None = Depends(require_admin_token)) -> dict[str, Any]:
    return {
        "dataDir": str(DATA_DIR),
        "datasets": [_dataset_payload(slug) for slug in DATASETS],
    }


@router.put("/datasets/{slug}/{collection_key}/{record_index}")
def update_record(
    slug: str,
    collection_key: str,
    record_index: int,
    payload: RecordPayload,
    _: None = Depends(require_admin_token),
) -> dict[str, Any]:
    data = _read_dataset(slug)
    records = _collection_records(data, collection_key)
    if record_index < 0 or record_index >= len(records):
        raise HTTPException(status_code=404, detail=f"Record index out of range: {record_index}")
    records[record_index] = payload.record
    _write_dataset(slug, data)
    return {"updated": True, "dataset": _dataset_payload(slug)}


@router.post("/datasets/{slug}/{collection_key}")
def create_record(
    slug: str,
    collection_key: str,
    payload: RecordPayload,
    _: None = Depends(require_admin_token),
) -> dict[str, Any]:
    data = _read_dataset(slug)
    records = _collection_records(data, collection_key)
    records.append(payload.record)
    _write_dataset(slug, data)
    return {"created": True, "index": len(records) - 1, "dataset": _dataset_payload(slug)}


@router.delete("/datasets/{slug}/{collection_key}/{record_index}")
def delete_record(
    slug: str,
    collection_key: str,
    record_index: int,
    _: None = Depends(require_admin_token),
) -> dict[str, Any]:
    data = _read_dataset(slug)
    records = _collection_records(data, collection_key)
    if record_index < 0 or record_index >= len(records):
        raise HTTPException(status_code=404, detail=f"Record index out of range: {record_index}")
    removed = records.pop(record_index)
    _write_dataset(slug, data)
    return {"deleted": True, "removed": removed, "dataset": _dataset_payload(slug)}
