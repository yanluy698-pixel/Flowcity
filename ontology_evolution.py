"""Analyze open-hypothesis feedback and propose ontology improvements."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from learning_events import LearningEventStore, default_db_path
from semantic_retrieval import cosine_similarity, RETRIEVER


CLUSTER_SIMILARITY = 0.82
MIN_SESSIONS = 20
MAX_DELETE_RATE = 0.15
MIN_CONFIRM_RATE = 0.35
MIN_CONFIRM_LOWER_BOUND = 0.2
MIN_CLUSTER_COHESION = 0.72
MIN_DECLARED_KEY_COHESION = 0.65
NEGATIVE_DELETE_RATE = 0.35
APPROVED_MATCH_SIMILARITY = 0.76
APPROVED_MATCH_MIN_LEXICAL_OVERLAP = 0.08
BLOCKED_PATTERN_MARGIN = 0.03
SALIENT_STOP_TERMS = {
    "出门",
    "时候",
    "通过",
    "安排",
    "避免",
    "减少",
    "需要",
    "希望",
    "一个",
}


def _proposal_id(cluster_key: str) -> str:
    return "proposal_" + hashlib.sha1(cluster_key.encode("utf-8")).hexdigest()[:12]


def _wilson_lower_bound(successes: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    proportion = successes / total
    denominator = 1 + (z * z / total)
    center = proportion + (z * z / (2 * total))
    margin = z * math.sqrt((proportion * (1 - proportion) / total) + (z * z / (4 * total * total)))
    return max(0.0, (center - margin) / denominator)


def _cluster_cohesion(cluster: dict[str, Any]) -> float:
    vectors = cluster.get("exampleVectors", [])
    center = cluster.get("centerVector", [])
    if not vectors or not center:
        return 0.0
    return sum(cosine_similarity(vector, center) for vector in vectors) / len(vectors)


def _session_outcomes(
    related: list[dict[str, Any]],
) -> dict[str, set[str]]:
    outcomes: dict[str, set[str]] = defaultdict(set)
    for event in related:
        outcomes[str(event["session_hash"])].add(str(event["event_type"]))
    return outcomes


def _report_status(
    *,
    session_count: int,
    delete_rate: float,
    confirm_rate: float,
    confirm_lower_bound: float,
    cohesion: float,
    minimum_cohesion: float,
) -> str:
    if session_count < MIN_SESSIONS:
        return "insufficient_data"
    if delete_rate >= NEGATIVE_DELETE_RATE and confirm_rate < MIN_CONFIRM_LOWER_BOUND:
        return "negative_pattern_blocked"
    if (
        delete_rate > MAX_DELETE_RATE
        or confirm_rate < MIN_CONFIRM_RATE
        or confirm_lower_bound < MIN_CONFIRM_LOWER_BOUND
        or cohesion < minimum_cohesion
    ):
        return "mixed_signal_observing"
    return "proposal_generated"


def analyze(store: LearningEventStore, *, persist_proposals: bool = True) -> dict[str, Any]:
    events = store.events()
    created = [event for event in events if event["event_type"] == "hypothesis_created"]
    clusters: list[dict[str, Any]] = []
    for event in created:
        text = str(event["payload"].get("text") or "")
        if not text:
            continue
        vector = RETRIEVER.embed([text])[0]
        event_cluster_key = str(event.get("cluster_key") or "")
        target = next(
            (cluster for cluster in clusters if event_cluster_key and cluster["clusterKey"] == event_cluster_key),
            None,
        )
        target_score = 1.0 if target is not None else 0.0
        if target is None:
            for cluster in clusters:
                score = cosine_similarity(vector, cluster["centerVector"])
                if score > target_score:
                    target = cluster
                    target_score = score
        if target is None or target_score < CLUSTER_SIMILARITY:
            clusters.append(
                {
                    "clusterKey": event_cluster_key or str(event.get("hypothesis_id") or f"cluster_{len(clusters)+1}"),
                    "groupingSource": "declared_key" if event_cluster_key else "vector_similarity",
                    "centerVector": vector,
                    "exampleVectors": [vector],
                    "examples": [text],
                    "hypothesisIds": [event.get("hypothesis_id")],
                }
            )
        else:
            target["examples"].append(text)
            target["exampleVectors"].append(vector)
            if event.get("hypothesis_id") not in target["hypothesisIds"]:
                target["hypothesisIds"].append(event.get("hypothesis_id"))
            count = len(target["examples"])
            target["centerVector"] = [
                ((old * (count - 1)) + new) / count
                for old, new in zip(target["centerVector"], vector)
            ]

    event_by_hypothesis: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event.get("hypothesis_id"):
            event_by_hypothesis[str(event["hypothesis_id"])].append(event)

    reports: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    for cluster in clusters:
        related = [
            event
            for hypothesis_id in cluster["hypothesisIds"]
            for event in event_by_hypothesis.get(str(hypothesis_id), [])
        ]
        outcomes = _session_outcomes(related)
        shown_sessions = {session for session, events in outcomes.items() if "hypothesis_shown" in events}
        deleted_sessions = {session for session, events in outcomes.items() if "hypothesis_deleted" in events}
        confirmed_sessions = {
            session
            for session, events in outcomes.items()
            if "plan_confirmed" in events and "hypothesis_deleted" not in events
        }
        modified_sessions = {session for session, events in outcomes.items() if "node_modified" in events}
        denominator = max(1, len(shown_sessions))
        delete_rate = len(deleted_sessions) / denominator
        confirm_rate = len(confirmed_sessions) / denominator
        confirm_lower_bound = _wilson_lower_bound(len(confirmed_sessions), len(shown_sessions))
        cohesion = _cluster_cohesion(cluster)
        minimum_cohesion = (
            MIN_DECLARED_KEY_COHESION
            if cluster.get("groupingSource") == "declared_key"
            else MIN_CLUSTER_COHESION
        )
        status = _report_status(
            session_count=len(shown_sessions),
            delete_rate=delete_rate,
            confirm_rate=confirm_rate,
            confirm_lower_bound=confirm_lower_bound,
            cohesion=cohesion,
            minimum_cohesion=minimum_cohesion,
        )
        report = {
            "clusterKey": cluster["clusterKey"],
            "examples": cluster["examples"][:6],
            "sessionCount": len(shown_sessions),
            "shownCount": len(shown_sessions),
            "confirmedSessionCount": len(confirmed_sessions),
            "deletedSessionCount": len(deleted_sessions),
            "deleteRate": round(delete_rate, 4),
            "confirmRate": round(confirm_rate, 4),
            "confirmLowerBound": round(confirm_lower_bound, 4),
            "nodeModifiedCount": len(modified_sessions),
            "semanticCohesion": round(cohesion, 4),
            "minimumSemanticCohesion": minimum_cohesion,
            "groupingSource": cluster.get("groupingSource"),
            "status": status,
        }
        if status == "proposal_generated":
            proposal = {
                "proposalId": _proposal_id(cluster["clusterKey"]),
                "proposalType": "approved_open_hypothesis_pattern",
                "clusterKey": cluster["clusterKey"],
                "status": "pending_review",
                "examples": cluster["examples"][:10],
                "metrics": report,
            }
            if persist_proposals:
                store.upsert_proposal(proposal)
            proposals.append(proposal)
        reports.append(report)
    return {"clusters": reports, "proposals": proposals, "eventCount": len(events)}


def _salient_terms(text: str) -> set[str]:
    chunks = [item for item in re.findall(r"[\u4e00-\u9fff]{2,}", str(text or "")) if item]
    terms: set[str] = set()
    for chunk in chunks:
        max_len = min(4, len(chunk))
        for size in range(2, max_len + 1):
            for index in range(0, len(chunk) - size + 1):
                term = chunk[index : index + size]
                if term not in SALIENT_STOP_TERMS:
                    terms.add(term)
    return terms


def _lexical_overlap(left: str, right: str) -> float:
    left_terms = _salient_terms(left)
    right_terms = _salient_terms(right)
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms)


def _blocked_pattern_similarity(
    *,
    text: str,
    query_vector: list[float],
    store: LearningEventStore,
) -> float:
    blocked_examples: list[str] = []
    for cluster in analyze(store, persist_proposals=False).get("clusters", []):
        if cluster.get("status") in {"negative_pattern_blocked", "mixed_signal_observing"}:
            blocked_examples.extend(str(item) for item in cluster.get("examples", []) if item)
    if not blocked_examples:
        return 0.0
    vectors = RETRIEVER.embed(blocked_examples)
    vector_score = max(cosine_similarity(query_vector, vector) for vector in vectors)
    lexical_score = max(_lexical_overlap(text, example) for example in blocked_examples)
    return max(vector_score, lexical_score)


def approved_hypothesis_matches(
    text: str,
    *,
    store: LearningEventStore | None = None,
    min_similarity: float = APPROVED_MATCH_SIMILARITY,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Return reviewed patterns that generalize to a new, unseen utterance."""
    if not text.strip():
        return []
    store = store or LearningEventStore()
    query_vector = RETRIEVER.embed([text])[0]
    blocked_similarity = _blocked_pattern_similarity(text=text, query_vector=query_vector, store=store)
    matches: list[dict[str, Any]] = []
    for row in store.proposals("approved"):
        proposal = row.get("payload", {})
        examples = [str(item) for item in proposal.get("examples", []) if item]
        if not examples:
            continue
        vectors = RETRIEVER.embed(examples)
        similarity = max(cosine_similarity(query_vector, vector) for vector in vectors)
        if similarity < min_similarity:
            continue
        lexical_overlap = max(_lexical_overlap(text, example) for example in examples)
        if lexical_overlap < APPROVED_MATCH_MIN_LEXICAL_OVERLAP:
            continue
        if blocked_similarity >= similarity - BLOCKED_PATTERN_MARGIN:
            continue
        matches.append(
            {
                "proposalId": row.get("proposal_id"),
                "clusterKey": proposal.get("clusterKey") or row.get("cluster_key"),
                "text": examples[0],
                "similarity": round(similarity, 4),
                "lexicalOverlap": round(lexical_overlap, 4),
                "blockedSimilarity": round(blocked_similarity, 4),
                "source": "approved_learning_pattern",
            }
        )
    matches.sort(key=lambda item: (-float(item["similarity"]), str(item["clusterKey"])))
    return matches[:limit]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=default_db_path())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--list-proposals", action="store_true")
    parser.add_argument("--approve")
    parser.add_argument("--reject")
    args = parser.parse_args()
    store = LearningEventStore(args.db)
    if args.approve or args.reject:
        proposal_id = args.approve or args.reject
        status = "approved" if args.approve else "rejected"
        updated = store.review_proposal(str(proposal_id), status)
        result = {"proposalId": proposal_id, "status": status, "updated": updated}
    elif args.list_proposals:
        result = {"proposals": store.proposals()}
    else:
        result = analyze(store)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
