from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.admin_auth import AdminAccess, require_admin_read_token, require_admin_write_token


FLOWCITY_ROOT = Path(__file__).resolve().parents[3]
if str(FLOWCITY_ROOT) not in sys.path:
    sys.path.insert(0, str(FLOWCITY_ROOT))

import supply_governance  # noqa: E402
import mock_api  # noqa: E402


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
    "subareas": {
        "label": "二级商圈",
        "filename": "mock_subareas.json",
        "collections": ["subareas"],
        "description": "开放可进入的步行街、商场、店铺带等时间窗补充节点，属于 open-access 供给，不要求座位或余票库存。",
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
        "label": "运行时影子表",
        "filename": "mock_runtime_status.json",
        "collections": [
            "activityRuntimeStatus",
            "restaurantRuntimeStatus",
            "routeRuntimeStatus",
            "dealRuntimeStatus",
        ],
        "description": "确认模拟执行时的 POI 一对一运行时状态，以及路线拥堵、团购库存等扩展动态状态。",
    },
}

PRICE_BUCKETS = (
    ("free", "免费/0元", 0, 0),
    ("low", "低价 <=30", 1, 30),
    ("mid", "中价 31-80", 31, 80),
    ("high", "高价 >80", 81, None),
)

POI_SUPPLY_PRINCIPLES = [
    "每个正式商圈至少覆盖活动、餐饮、过渡补位三类供给。",
    "每个商圈尽量同时有免费/低价/中价/高价候选，避免预算一紧就无解。",
    "POI 标签优先写事实画像：人群、体力、噪声、可坐下、室内外、预约/排队、消费层级；不要把某个用户故事写成标签。",
    "补位点只作为空窗、等位、短休息节点，不和主活动混为一谈。",
    "点名目的地区域必须保留进入下一轮；探索区域才参与粗排淘汰。",
    "POI 运行时影子表与活动/餐厅一对一覆盖，其中约 40% 变化，用于验证确认前校验和异常重规划。",
]


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
    mock_api.clear_mock_data_cache()


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


def _price_bucket(value: Any) -> str:
    try:
        price = float(value or 0)
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0:
        return "free"
    if price <= 30:
        return "low"
    if price <= 80:
        return "mid"
    return "high"


def _bucket_counts(records: list[dict[str, Any]], price_key: str) -> dict[str, int]:
    counts = {key: 0 for key, *_ in PRICE_BUCKETS}
    for record in records:
        counts[_price_bucket(record.get(price_key))] += 1
    return counts


def _tag_counts(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        values = record.get(key, [])
        if not isinstance(values, list):
            continue
        for value in values:
            tag = str(value)
            counts[tag] = counts.get(tag, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10])


def _runtime_record_changed(record: dict[str, Any]) -> bool:
    return record.get("runtimeState") == "changed" or record.get("eventType") not in (None, "none", "unchanged")


def _runtime_ratio(runtime_data: dict[str, Any]) -> dict[str, Any]:
    poi_rows = [
        record
        for key in ("activityRuntimeStatus", "restaurantRuntimeStatus")
        for record in runtime_data.get(key, [])
        if isinstance(record, dict)
    ]
    extension_rows = [
        record
        for key in ("routeRuntimeStatus", "dealRuntimeStatus")
        for record in runtime_data.get(key, [])
        if isinstance(record, dict)
    ]
    rows = poi_rows
    total = len(rows)
    abnormal = sum(1 for record in rows if _runtime_record_changed(record))
    extension_changed = sum(1 for record in extension_rows if _runtime_record_changed(record))
    ratio = abnormal / total if total else 0.0
    return {
        "total": total,
        "abnormal": abnormal,
        "normal": total - abnormal,
        "abnormalRatio": round(ratio, 3),
        "targetAbnormalRatio": float(runtime_data.get("abnormalShare", 0.4)),
        "withinTolerance": abs(ratio - float(runtime_data.get("abnormalShare", 0.4))) <= 0.08,
        "scope": "activityRuntimeStatus + restaurantRuntimeStatus",
        "activityRuntimeTotal": len(runtime_data.get("activityRuntimeStatus", [])),
        "restaurantRuntimeTotal": len(runtime_data.get("restaurantRuntimeStatus", [])),
        "extensionRuntimeTotal": len(extension_rows),
        "extensionRuntimeChanged": extension_changed,
    }


def _coverage_payload() -> dict[str, Any]:
    areas = [area for area in _read_dataset("areas").get("areas", []) if not str(area.get("areaId", "")).startswith("origin_")]
    activities = supply_governance.enrich_many(_read_dataset("activities").get("activities", []), source_type="mock_curated")
    restaurants = supply_governance.enrich_many(_read_dataset("restaurants").get("restaurants", []), source_type="mock_curated")
    subareas = supply_governance.enrich_many(_read_dataset("subareas").get("subareas", []), source_type="mock_open_access_subarea")
    runtime_data = _read_dataset("runtime_status")
    runtime_status = _runtime_ratio(runtime_data)
    governance_coverage = supply_governance.coverage([*activities, *restaurants, *subareas])
    area_rows: list[dict[str, Any]] = []
    for area in areas:
        area_id = str(area.get("areaId") or "")
        area_activities = [item for item in activities if item.get("areaId") == area_id]
        area_restaurants = [item for item in restaurants if item.get("areaId") == area_id]
        area_subareas = [item for item in subareas if item.get("areaId") == area_id]
        activity_buckets = _bucket_counts(area_activities, "pricePerPerson")
        restaurant_buckets = _bucket_counts(area_restaurants, "avgPricePerPerson")
        filler_count = sum(1 for item in area_activities if item.get("isFiller"))
        gaps: list[str] = []
        if len(area_activities) < 8:
            gaps.append("活动候选偏少")
        if len(area_restaurants) < 8:
            gaps.append("餐厅候选偏少")
        if filler_count + len(area_subareas) < 2:
            gaps.append("过渡补位点不足")
        if not (activity_buckets["free"] or activity_buckets["low"]):
            gaps.append("活动缺免费/低价层")
        if not restaurant_buckets["low"]:
            gaps.append("餐饮缺低价层")
        if not restaurant_buckets["high"]:
            gaps.append("餐饮缺高价层")
        area_rows.append(
            {
                "areaId": area_id,
                "areaName": area.get("name"),
                "activityCount": len(area_activities),
                "restaurantCount": len(area_restaurants),
                "fillerCount": filler_count,
                "subareaCount": len(area_subareas),
                "openAccessStatus": "open_access_not_inventory",
                "activityPriceBuckets": activity_buckets,
                "restaurantPriceBuckets": restaurant_buckets,
                "activityAudienceTags": _tag_counts(area_activities, "audienceTags"),
                "restaurantAudienceTags": _tag_counts(area_restaurants, "audienceTags"),
                "gaps": gaps,
            }
        )
    return {
        "principles": POI_SUPPLY_PRINCIPLES,
        "priceBuckets": [{"key": key, "label": label, "min": minimum, "max": maximum} for key, label, minimum, maximum in PRICE_BUCKETS],
        "kpis": {
            "areaCount": len(areas),
            "activityCount": len(activities),
            "restaurantCount": len(restaurants),
            "subareaCount": len(subareas),
            "openAccessCount": len(subareas),
            "poiCount": len(activities) + len(restaurants),
            "fillerCount": sum(1 for item in activities if item.get("isFiller")),
            "poiRuntimeTotal": runtime_status["total"],
            "poiRuntimeChanged": runtime_status["abnormal"],
            "extensionRuntimeTotal": runtime_status["extensionRuntimeTotal"],
            "extensionRuntimeChanged": runtime_status["extensionRuntimeChanged"],
        },
        "governanceCoverage": governance_coverage,
        "areas": area_rows,
        "runtimeStatus": runtime_status,
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
def list_datasets(access: AdminAccess = Depends(require_admin_read_token)) -> dict[str, Any]:
    return {
        "access": access,
        "dataDir": str(DATA_DIR),
        "datasets": [_dataset_payload(slug) for slug in DATASETS],
    }


@router.get("/coverage")
def coverage_report(access: AdminAccess = Depends(require_admin_read_token)) -> dict[str, Any]:
    payload = _coverage_payload()
    payload["access"] = access
    return payload


@router.put("/datasets/{slug}/{collection_key}/{record_index}")
def update_record(
    slug: str,
    collection_key: str,
    record_index: int,
    payload: RecordPayload,
    _: AdminAccess = Depends(require_admin_write_token),
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
    _: AdminAccess = Depends(require_admin_write_token),
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
    _: AdminAccess = Depends(require_admin_write_token),
) -> dict[str, Any]:
    data = _read_dataset(slug)
    records = _collection_records(data, collection_key)
    if record_index < 0 or record_index >= len(records):
        raise HTTPException(status_code=404, detail=f"Record index out of range: {record_index}")
    removed = records.pop(record_index)
    _write_dataset(slug, data)
    return {"deleted": True, "removed": removed, "dataset": _dataset_payload(slug)}
