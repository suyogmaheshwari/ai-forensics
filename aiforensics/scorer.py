"""Additive weighted scoring with per-group caps, a minimum number of groups,
deny features and a threshold. One eligible product -> identified; several ->
conflict; none -> unknown.
"""
from __future__ import annotations

from .features import extract
from .rules import feature_matches


def score_rule(rule: dict, fx: dict) -> dict:
    if rule.get("family", "any") not in ("any", fx["family"]):
        return {"product": rule["product"], "score": 0, "eligible": False, "matched": [], "denied": False, "reason": "family mismatch"}
    for deny in rule.get("deny", []):
        matched, _ = feature_matches(deny, fx)
        if matched:
            return {"product": rule["product"], "score": 0, "eligible": False, "matched": [], "denied": True, "reason": "deny feature matched"}
    group_scores: dict = {}
    matched = []
    for feature in rule["features"]:
        ok, strength = feature_matches(feature, fx)
        if not ok:
            continue
        group = feature["group"]
        group_scores[group] = group_scores.get(group, 0) + feature["weight"] * strength
        matched.append(feature["id"])
    total = 0.0
    for group, s in group_scores.items():
        cap = rule.get("group_caps", {}).get(group)
        total += min(s, cap) if cap is not None else s
    total = min(total, 100)
    eligible = total >= rule["threshold"] and len(group_scores) >= rule.get("min_groups", 1)
    return {"product": rule["product"], "score": round(total), "eligible": eligible, "matched": matched, "denied": False, "reason": ""}


def identify(record: dict, rules: list) -> dict:
    fx = extract(record)
    scores = [score_rule(r, fx) for r in rules]
    eligible = [s for s in scores if s["eligible"]]
    if len(eligible) == 1:
        decision, product = "identified", eligible[0]["product"]
    elif len(eligible) > 1:
        decision, product = "conflict", None
    else:
        decision, product = "unknown", None
    return {"decision": decision, "product": product, "family": fx["family"], "scores": sorted(scores, key=lambda s: -s["score"]), "features": fx}
