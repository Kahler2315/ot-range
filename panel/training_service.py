"""Backend-authoritative training lifecycle and reporting operations."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from panel.storage import Storage, StorageConflict, utc_now
from scenarios.catalog import LEARNING_OBJECTIVES, SCENARIOS, SCENARIOS_BY_ID, Scenario
from scenarios.flags import FLAGS_BY_SCENARIO, Flag
from scenarios.flags import check as check_flag
from scenarios.scoring import hint_cost, remaining_points, scenario_max_points

SOLUTION_DOCS = {"answer-key"}
WALKTHROUGH_DOCS = {"detection", "expected-impact", "walkthrough"}
SOLUTION_BEARING_DOCS = SOLUTION_DOCS | WALKTHROUGH_DOCS
VALID_MODES = {"independent", "guided"}
APPLICATION_VERSION = "0.1.0"
REPORT_SCHEMA_VERSION = 2
LOCAL_PROGRESS_DISCLAIMER = (
    "Local training progress only — stored on this installation, not tamper-resistant, "
    "not suitable as formal certification evidence."
)


class TrainingError(RuntimeError):
    status_code = 400


class TrainingPolicyDenied(TrainingError):
    status_code = 403


class TrainingLocked(TrainingError):
    status_code = 409


class TrainingNotFound(TrainingError):
    status_code = 404


def empty_state(scenario_id: str, *, scored: bool = True) -> dict[str, Any]:
    return {
        "scenarioId": scenario_id,
        "attemptId": None,
        "attemptNumber": 0,
        "status": "not_started",
        "mode": None,
        "scored": scored,
        "startedAt": None,
        "completedAt": None,
        "flagAttempts": {},
        "hintsRevealed": {},
        "flagsSolved": [],
        "pointsEarned": {},
        "documentsOpened": [],
        "documentHistory": [],
        "walkthroughOpened": False,
        "answerKeyOpened": False,
        "solutionLocked": False,
        "priorSolutionExposure": False,
        "executions": [],
        "notes": "",
    }


def serialize_attempt(
    attempt: dict[str, Any] | None,
    scenario_id: str,
    *,
    scored_default: bool = True,
) -> dict[str, Any]:
    if attempt is None:
        return empty_state(scenario_id, scored=scored_default)
    flag_attempts = {item["flag_id"]: item["attempt_count"] for item in attempt["flags"]}
    flags_solved = [item["flag_id"] for item in attempt["flags"] if item["solved"]]
    points_earned = {
        item["flag_id"]: item["points_earned"]
        for item in attempt["flags"]
        if item["solved"] and item["points_earned"] is not None
    }
    hints: dict[str, list[int]] = {}
    for item in attempt["hints"]:
        hints.setdefault(item["flag_id"], []).append(item["level"])
    documents = [item["document_key"] for item in attempt["documents"]]
    return {
        "scenarioId": scenario_id,
        "attemptId": attempt["id"],
        "attemptNumber": attempt["attempt_number"],
        "status": attempt["status"],
        "mode": attempt["mode"],
        "scored": bool(attempt["scored"]),
        "startedAt": attempt["started_at"],
        "completedAt": attempt["completed_at"],
        "flagAttempts": flag_attempts,
        "hintsRevealed": hints,
        "flagsSolved": flags_solved,
        "pointsEarned": points_earned,
        "documentsOpened": documents,
        "documentHistory": [dict(item) for item in attempt["documents"]],
        "walkthroughOpened": any(key in WALKTHROUGH_DOCS for key in documents),
        "answerKeyOpened": any(key in SOLUTION_DOCS for key in documents),
        "solutionLocked": bool(attempt["solution_locked"]),
        "priorSolutionExposure": bool(attempt["prior_solution_exposure"]),
        "executions": [dict(item) for item in attempt.get("executions", [])],
        "notes": attempt["notes"],
    }


class TrainingService:
    def __init__(self, storage: Storage):
        self.storage = storage

    def policies(self) -> dict[str, Any]:
        return self.storage.get_policies()

    def public_policies(self) -> dict[str, Any]:
        policies = self.policies()
        return {
            "scoredModeEnabled": policies["scored_mode_enabled"],
            "independentModeEnabled": policies["independent_mode_enabled"],
            "guidedModeEnabled": policies["guided_mode_enabled"],
            "hintsEnabled": policies["hints_enabled"],
            "answerKeyEnabled": policies["answer_key_enabled"],
            "walkthroughEnabled": policies["walkthrough_enabled"],
            "scenarioAvailability": policies["scenario_availability"],
        }

    def require_scenario(self, scenario_id: str) -> Scenario:
        scenario = SCENARIOS_BY_ID.get(scenario_id) if isinstance(scenario_id, str) else None
        if scenario is None:
            raise TrainingNotFound("unknown scenario")
        if self.policies()["scenario_availability"].get(scenario_id, True) is False:
            raise TrainingPolicyDenied(
                "This scenario is unavailable under current training policies."
            )
        return scenario

    def validate_mode(self, mode: str | None) -> str:
        policies = self.policies()
        if mode is None:
            if policies["independent_mode_enabled"]:
                return "independent"
            if policies["guided_mode_enabled"]:
                return "guided"
            raise TrainingPolicyDenied("No training mode is currently enabled.")
        if not isinstance(mode, str) or mode not in VALID_MODES:
            raise TrainingError("unknown training mode")
        if not policies[f"{mode}_mode_enabled"]:
            raise TrainingPolicyDenied(f"{mode.title()} mode is disabled by the instructor.")
        return mode

    def all_state(self, profile_id: str) -> dict[str, Any]:
        public_policies = self.public_policies()
        attempts = {
            attempt["scenario_id"]: attempt for attempt in self.storage.list_attempts(profile_id)
        }
        return {
            "profileId": profile_id,
            "policies": public_policies,
            "scenarios": {
                scenario.id: serialize_attempt(
                    attempts.get(scenario.id),
                    scenario.id,
                    scored_default=public_policies["scoredModeEnabled"],
                )
                for scenario in SCENARIOS
            },
        }

    def state(self, profile_id: str, scenario_id: str) -> dict[str, Any]:
        self.require_scenario(scenario_id)
        return serialize_attempt(
            self.storage.get_attempt(profile_id, scenario_id),
            scenario_id,
            scored_default=self.policies()["scored_mode_enabled"],
        )

    def configure_attempt(
        self,
        profile_id: str,
        scenario_id: str,
        *,
        mode: str | None,
        start: bool,
    ) -> dict[str, Any]:
        self.require_scenario(scenario_id)
        selected_mode = self.validate_mode(mode)
        try:
            policies = self.policies()
            attempt = self.storage.ensure_attempt(
                profile_id,
                scenario_id,
                mode=selected_mode,
                scored=policies["scored_mode_enabled"],
                start=start,
                policy_snapshot={
                    "scoredModeEnabled": policies["scored_mode_enabled"],
                    "independentModeEnabled": policies["independent_mode_enabled"],
                    "guidedModeEnabled": policies["guided_mode_enabled"],
                    "hintsEnabled": policies["hints_enabled"],
                    "answerKeyEnabled": policies["answer_key_enabled"],
                    "walkthroughEnabled": policies["walkthrough_enabled"],
                    "scenarioEnabled": policies["scenario_availability"].get(scenario_id, True),
                },
            )
        except StorageConflict as exc:
            raise TrainingLocked(str(exc)) from exc
        return serialize_attempt(attempt, scenario_id)

    def set_notes(self, profile_id: str, scenario_id: str, notes: str) -> dict[str, Any]:
        self.require_scenario(scenario_id)
        if not isinstance(notes, str):
            raise TrainingError("notes must be text")
        if len(notes) > 20_000:
            raise TrainingError("notes are too long")
        existing = self.storage.get_attempt(profile_id, scenario_id)
        if existing is None:
            self.configure_attempt(profile_id, scenario_id, mode=None, start=False)
        self.storage.set_notes(profile_id, scenario_id, notes)
        return self.state(profile_id, scenario_id)

    def flag_payload(self) -> dict[str, list[dict[str, Any]]]:
        policies = self.policies()
        result = {}
        for scenario_id, flags in FLAGS_BY_SCENARIO.items():
            if policies["scenario_availability"].get(scenario_id, True) is False:
                continue
            result[scenario_id] = [self._safe_flag(flag) for flag in flags]
        return result

    @staticmethod
    def _safe_flag(flag: Flag) -> dict[str, Any]:
        return {
            "id": flag.id,
            "prompt": flag.prompt,
            "points": flag.points,
            "hintCosts": [hint_cost(flag.points, level) for level in range(1, len(flag.hints) + 1)],
            "category": flag.category,
            "evidenceSource": flag.evidence_source,
            "objectives": [LEARNING_OBJECTIVES[oid] for oid in flag.objective_ids],
        }

    def reveal_hint(
        self,
        profile_id: str,
        scenario_id: str,
        flag_id: str,
        level: int,
    ) -> dict[str, Any]:
        self.require_scenario(scenario_id)
        if not self.policies()["hints_enabled"]:
            raise TrainingPolicyDenied("Hints are disabled by the instructor.")
        flag = self._flag(scenario_id, flag_id)
        if level < 1 or level > len(flag.hints):
            raise TrainingNotFound("unknown hint level")
        state = self.state(profile_id, scenario_id)
        mode = state["mode"]
        if state["status"] == "not_started":
            state = self.configure_attempt(profile_id, scenario_id, mode=mode, start=True)
            mode = state["mode"]
        cost = 0 if mode == "guided" or not state["scored"] else hint_cost(flag.points, level)
        try:
            is_new = self.storage.reveal_hint(profile_id, scenario_id, flag_id, level, cost)
        except StorageConflict as exc:
            raise TrainingLocked(str(exc)) from exc
        return {
            "text": flag.hints[level - 1].text,
            "cost": cost,
            "newlyRevealed": is_new,
            "state": self.state(profile_id, scenario_id),
        }

    def submit_flag(
        self,
        profile_id: str,
        scenario_id: str,
        flag_id: str,
        answer: str,
        mode: str | None = None,
    ) -> dict[str, Any]:
        self.require_scenario(scenario_id)
        if not isinstance(answer, str):
            raise TrainingError("answer must be text")
        flag = self._flag(scenario_id, flag_id)
        state = self.state(profile_id, scenario_id)
        selected_mode = state["mode"] or mode
        if state["status"] == "not_started":
            state = self.configure_attempt(profile_id, scenario_id, mode=selected_mode, start=True)
        correct = check_flag(flag, answer)
        levels = state["hintsRevealed"].get(flag_id, [])
        points = None
        if correct and state["scored"]:
            points = (
                flag.points if state["mode"] == "guided" else remaining_points(flag.points, levels)
            )
        try:
            attempt = self.storage.record_flag_submission(
                profile_id,
                scenario_id,
                flag_id,
                correct=correct,
                points_earned=points,
                all_flag_ids=[item.id for item in FLAGS_BY_SCENARIO[scenario_id]],
            )
        except StorageConflict as exc:
            raise TrainingLocked(str(exc)) from exc
        return {"correct": correct, "state": serialize_attempt(attempt, scenario_id)}

    def open_document(
        self,
        profile_id: str,
        scenario_id: str,
        document_key: str,
    ) -> dict[str, Any]:
        self.require_scenario(scenario_id)
        policies = self.policies()
        if document_key in SOLUTION_DOCS and not policies["answer_key_enabled"]:
            raise TrainingPolicyDenied("Answer key access is disabled by the instructor.")
        if document_key in WALKTHROUGH_DOCS and not policies["walkthrough_enabled"]:
            raise TrainingPolicyDenied("Solution documentation is disabled by the instructor.")
        state = self.state(profile_id, scenario_id)
        if state["status"] == "not_started":
            state = self.configure_attempt(profile_id, scenario_id, mode=state["mode"], start=True)
        if (
            document_key in SOLUTION_BEARING_DOCS
            and state["mode"] == "independent"
            and state["status"]
            not in ("completed", "completed_with_assistance", "solution_revealed")
        ):
            raise TrainingPolicyDenied(
                "This solution-bearing document requires explicit solution reveal."
            )
        solution = document_key in SOLUTION_BEARING_DOCS
        attempt = self.storage.record_document_open(
            profile_id,
            scenario_id,
            document_key,
            solution=solution,
            lock_solution=False,
        )
        return serialize_attempt(attempt, scenario_id)

    def reveal_solution_document(
        self, profile_id: str, scenario_id: str, document_key: str
    ) -> dict[str, Any]:
        """Explicitly transition an independent attempt before returning solution material."""
        if document_key not in SOLUTION_BEARING_DOCS:
            raise TrainingNotFound("unknown solution document")
        self.require_scenario(scenario_id)
        policies = self.policies()
        if document_key in SOLUTION_DOCS and not policies["answer_key_enabled"]:
            raise TrainingPolicyDenied("Answer key access is disabled by the instructor.")
        if document_key in WALKTHROUGH_DOCS and not policies["walkthrough_enabled"]:
            raise TrainingPolicyDenied("Solution documentation is disabled by the instructor.")
        state = self.state(profile_id, scenario_id)
        if state["status"] == "not_started":
            state = self.configure_attempt(profile_id, scenario_id, mode=state["mode"], start=True)
        attempt = self.storage.record_document_open(
            profile_id,
            scenario_id,
            document_key,
            solution=True,
            lock_solution=state["mode"] == "independent"
            and state["status"] not in ("completed", "completed_with_assistance"),
        )
        return serialize_attempt(attempt, scenario_id)

    def authorize_overlay(self, profile_id: str, scenario_id: str) -> None:
        self.require_scenario(scenario_id)
        state = self.state(profile_id, scenario_id)
        allowed = state["mode"] == "guided" or state["status"] in (
            "completed",
            "completed_with_assistance",
            "solution_revealed",
        )
        if not allowed:
            raise TrainingPolicyDenied(
                "This scenario overlay is hidden until completion or explicit solution reveal."
            )
        if state["mode"] == "guided" and state["attemptId"]:
            self.storage.record_training_event(
                profile_id,
                "scenario_overlay_opened",
                attempt_id=state["attemptId"],
                scenario_id=scenario_id,
                details={"guided_assistance": True},
            )

    @staticmethod
    def _flag(scenario_id: str, flag_id: str) -> Flag:
        flag = next(
            (item for item in FLAGS_BY_SCENARIO.get(scenario_id, []) if item.id == flag_id),
            None,
        )
        if flag is None:
            raise TrainingNotFound("unknown flag")
        return flag

    def reset_attempt(
        self, profile_id: str, scenario_id: str, *, actor_type: str = "student"
    ) -> None:
        self.require_scenario(scenario_id)
        self.storage.reset_attempt(profile_id, scenario_id, actor_type=actor_type)

    def reset_all(self, profile_id: str, *, actor_type: str = "student") -> None:
        self.storage.reset_profile_progress(profile_id, actor_type=actor_type)

    def profile_report(self, profile_id: str, *, actor_type: str = "student") -> dict[str, Any]:
        profile = self.storage.get_profile(profile_id)
        if profile is None:
            raise TrainingNotFound("unknown profile")
        reports = [
            self.scenario_report(
                profile_id, scenario, include_instructor_fields=actor_type == "instructor"
            )
            for scenario in SCENARIOS
        ]
        history = self.storage.list_attempt_history(profile_id)
        events = self.storage.list_training_events(profile_id=profile_id, limit=1000)
        solution_events = [
            event
            for event in events
            if event["event_type"] in ("answer_key_opened", "solution_document_opened")
        ]
        reset_events = [event for event in events if event["event_type"] == "attempt_reset"]
        report = {
            "reportSchemaVersion": REPORT_SCHEMA_VERSION,
            "applicationVersion": APPLICATION_VERSION,
            "sourceRevision": os.environ.get("OT_RANGE_BUILD_REVISION"),
            "databaseId": self.storage.database_id(),
            "profile": {
                "displayName": profile["display_name"],
                "learnerId": profile["learner_id"],
                "organization": profile["organization"],
                "course": profile["course"],
                "section": profile["section"],
                "instructorName": profile["instructor_name"],
                "localProfileId": profile["id"],
            },
            "generatedAt": utc_now(),
            "disclaimer": LOCAL_PROGRESS_DISCLAIMER,
            "integritySummary": {
                "totalAttempts": len(history),
                "totalResets": len(reset_events),
                "studentResets": sum(e["actor_type"] == "student" for e in reset_events),
                "instructorResets": sum(e["actor_type"] == "instructor" for e in reset_events),
                "priorSolutionReveals": sum(
                    e["event_type"] == "answer_key_opened" for e in solution_events
                ),
                "priorSolutionDocumentViews": sum(
                    e["event_type"] == "solution_document_opened" for e in solution_events
                ),
                "recentIntegrityEvents": [
                    {
                        "scenario": event["scenario_id"],
                        "eventType": event["event_type"],
                        "actorType": event["actor_type"],
                        "occurredAt": event["occurred_at"],
                        "details": event["details"],
                    }
                    for event in events
                    if event["event_type"]
                    in (
                        "answer_key_opened",
                        "solution_document_opened",
                        "attempt_reset",
                        "profile_progress_reset",
                    )
                ][:25],
            },
            "scenarios": reports,
        }
        self.storage.record_training_event(
            profile_id,
            "report_exported",
            actor_type=actor_type,
            details={"report_schema_version": REPORT_SCHEMA_VERSION},
        )
        return report

    def scenario_report(
        self,
        profile_id: str,
        scenario: Scenario,
        *,
        include_instructor_fields: bool = False,
    ) -> dict[str, Any]:
        state = serialize_attempt(
            self.storage.get_attempt(profile_id, scenario.id),
            scenario.id,
            scored_default=self.policies()["scored_mode_enabled"],
        )
        flags = FLAGS_BY_SCENARIO.get(scenario.id, [])
        earned = sum(state["pointsEarned"].values()) if state["scored"] else None
        max_points = scenario_max_points(flags) if state["scored"] else None
        incorrect = sum(
            max(0, count - (1 if flag_id in state["flagsSolved"] else 0))
            for flag_id, count in state["flagAttempts"].items()
        )
        hints = sum(len(levels) for levels in state["hintsRevealed"].values())
        duration = None
        if state["startedAt"] and state["completedAt"]:
            duration = int(
                (
                    datetime.fromisoformat(state["completedAt"])
                    - datetime.fromisoformat(state["startedAt"])
                ).total_seconds()
                * 1000
            )
        history = self.storage.list_attempt_history(profile_id, scenario.id)
        history_reports = [self._attempt_report(item, scenario) for item in history]
        historical_scores = [item["score"] for item in history_reports if item["score"] is not None]
        result = {
            "scenario": scenario.id,
            "scenarioTitle": scenario.title,
            "attemptId": state["attemptId"],
            "attemptNumber": state["attemptNumber"],
            "totalAttempts": len(history),
            "attemptStatus": state["status"],
            "trainingMode": state["mode"],
            "score": earned,
            "bestScore": max(historical_scores) if historical_scores else earned,
            "maximumScore": max_points,
            "flagsSolved": len(state["flagsSolved"]),
            "totalFlags": len(flags),
            "hintsUsed": hints,
            "incorrectAttempts": incorrect,
            "startedAt": state["startedAt"],
            "completedAt": state["completedAt"],
            "durationMs": duration,
            "learningObjectives": [LEARNING_OBJECTIVES[item] for item in scenario.objectives],
            "documentationOpened": state["documentsOpened"],
            "documentationHistory": state["documentHistory"],
            "solutionRevealed": state["answerKeyOpened"],
            "priorSolutionExposure": state["priorSolutionExposure"],
            "executionCount": len(state["executions"]),
            "successfulExecutionCount": sum(
                execution["return_code"] == 0 for execution in state["executions"]
            ),
            "executions": state["executions"],
            "notes": state["notes"],
            "attemptHistory": history_reports,
        }
        result["practicalExecutionVerified"] = result["successfulExecutionCount"] > 0
        result["completionClaim"] = (
            "scenario_run_verified"
            if result["practicalExecutionVerified"]
            else "knowledge_checks_only"
        )
        if include_instructor_fields or state["status"] in (
            "completed",
            "completed_with_assistance",
            "solution_revealed",
        ):
            result["detectionOutcome"] = scenario.caught_by
        return result

    def _attempt_report(self, attempt: dict[str, Any], scenario: Scenario) -> dict[str, Any]:
        state = serialize_attempt(attempt, scenario.id)
        flags = FLAGS_BY_SCENARIO.get(scenario.id, [])
        earned = sum(state["pointsEarned"].values()) if state["scored"] else None
        incorrect = sum(
            max(0, count - (1 if flag_id in state["flagsSolved"] else 0))
            for flag_id, count in state["flagAttempts"].items()
        )
        successful_executions = sum(
            execution["return_code"] == 0 for execution in state["executions"]
        )
        return {
            "attemptId": state["attemptId"],
            "attemptNumber": state["attemptNumber"],
            "current": bool(attempt["is_current"]),
            "status": state["status"],
            "mode": state["mode"],
            "scored": state["scored"],
            "score": earned,
            "maximumScore": scenario_max_points(flags) if state["scored"] else None,
            "flagsSolved": len(state["flagsSolved"]),
            "hintsUsed": sum(len(levels) for levels in state["hintsRevealed"].values()),
            "incorrectAttempts": incorrect,
            "startedAt": state["startedAt"],
            "completedAt": state["completedAt"],
            "closedAt": attempt["closed_at"],
            "resetAt": attempt["reset_at"],
            "resetActor": attempt["reset_actor"],
            "solutionRevealed": state["answerKeyOpened"],
            "priorSolutionExposure": state["priorSolutionExposure"],
            "documentsOpened": state["documentHistory"],
            "hints": [dict(item) for item in attempt["hints"]],
            "executions": state["executions"],
            "successfulExecutionCount": successful_executions,
            "practicalExecutionVerified": successful_executions > 0,
            "completionClaim": (
                "scenario_run_verified" if successful_executions else "knowledge_checks_only"
            ),
            "policySnapshot": attempt["policy_snapshot"],
            "notes": state["notes"],
            "practiceAfterSolutionReview": state["priorSolutionExposure"],
        }
