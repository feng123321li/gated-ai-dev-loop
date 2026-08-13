from __future__ import annotations

from typing import Any


RULE_VERSION = "assurance-v1"


def recommend_assurance_profile(
    *,
    task_summary: str,
    root_task_count: int,
    project_count: int,
    change_scope: str,
    risk_factors: list[str],
    verification_plan: str,
    risk_level: str,
    **_: Any,
) -> dict[str, Any]:
    """Recommend LIGHT or STANDARD from explicit, auditable facts."""

    blockers: list[dict[str, str]] = []
    if root_task_count != 1:
        blockers.append(
            {
                "rule": "SINGLE_ROOT_TASK_REQUIRED",
                "reason": "LIGHT requires exactly one root TASK.",
            }
        )
    if project_count != 1:
        blockers.append(
            {
                "rule": "SINGLE_PROJECT_REQUIRED",
                "reason": "LIGHT requires exactly one project workspace.",
            }
        )
    if change_scope != "LOCAL":
        blockers.append(
            {
                "rule": "LOCAL_SCOPE_REQUIRED",
                "reason": (
                    "LIGHT requires a local change with no cross-module or "
                    "cross-project seam."
                ),
            }
        )
    if risk_factors:
        blockers.append(
            {
                "rule": "HIGH_IMPACT_FACTORS_ABSENT",
                "reason": (
                    "LIGHT is unavailable when high-impact risk factors are "
                    f"present: {', '.join(sorted(risk_factors))}."
                ),
            }
        )
    if verification_plan != "TARGETED":
        blockers.append(
            {
                "rule": "TARGETED_VERIFICATION_REQUIRED",
                "reason": "LIGHT requires a known targeted verification command.",
            }
        )
    if risk_level != "LOW":
        blockers.append(
            {
                "rule": "LOW_RISK_REQUIRED",
                "reason": "LIGHT requires an explicitly classified LOW risk level.",
            }
        )

    light_eligible = not blockers
    if light_eligible:
        reasons = [
            "Exactly one root TASK in one project.",
            "Scope is local and no high-impact risk factor is present.",
            "Risk is LOW and targeted verification is known.",
        ]
    else:
        reasons = [item["reason"] for item in blockers]
    return {
        "ruleVersion": RULE_VERSION,
        "deterministic": True,
        "recommendedProfile": "LIGHT" if light_eligible else "STANDARD",
        "lightEligible": light_eligible,
        "reasons": reasons,
        "blockingRules": blockers,
        "inputClassification": {
            "taskSummary": task_summary,
            "rootTaskCount": root_task_count,
            "projectCount": project_count,
            "changeScope": change_scope,
            "riskFactors": sorted(risk_factors),
            "verificationPlan": verification_plan,
            "riskLevel": risk_level,
        },
        "guidance": (
            "Use recommendedProfile and copy the reasons into "
            "delivery.assuranceRationale. Re-run this recommendation whenever "
            "the classified facts change."
        ),
    }


__all__ = ("RULE_VERSION", "recommend_assurance_profile")
