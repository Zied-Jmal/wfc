# orchestrator/engine.py
# Core scenario engine - independent of FastAPI/MQTT wiring.
# Concepts:
#   Stage     - one expected step in the flow (e.g. "RuleEngine dispatches
#               RESPOND_TO_FIRE"). A stage PASSES when a matching MQTT message
#               (or absence of one, for negative checks) is observed within
#               its timeout window.
#   Scenario  - an ordered list of Stages representing one end-to-end flow.
#   Run       - one execution of a Scenario, producing a RunReport.
# Skip support:
#   If a stage times out, the run does not abort. It is marked FAILED, and
#   the engine injects a synthetic "skip event" (an MQTT publish on the
#   topic the next stage needs) so downstream stages can still be evaluated
#   independently. This lets you find ALL broken links in one run instead of
#   stopping at the first one.
# Replay support:
#   Each stage records the raw MQTT messages observed during its window so
#   the UI can show exact payloads ("what function used what").

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class StageStatus(str, Enum):
    PENDING  = "PENDING"
    RUNNING  = "RUNNING"
    PASSED   = "PASSED"
    FAILED   = "FAILED"
    SKIPPED  = "SKIPPED"
    TIMEOUT  = "TIMEOUT"


class RunStatus(str, Enum):
    PENDING  = "PENDING"
    RUNNING  = "RUNNING"
    PASSED   = "PASSED"
    FAILED   = "FAILED"
    ABORTED  = "ABORTED"


@dataclass
class MQTTObservation:
    """One MQTT message captured during a stage's observation window."""

    topic:     str
    payload:   Any
    qos:       int
    retain:    bool
    ts:        float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic, "payload": self.payload,
            "qos": self.qos, "retain": self.retain, "ts": self.ts,
        }


@dataclass
class StageResult:
    """Result of running a single stage."""

    stage_id:        str
    name:            str
    status:          StageStatus = StageStatus.PENDING
    started_at:      float | None = None
    finished_at:     float | None = None
    observations:    list[MQTTObservation] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    matched:         MQTTObservation | None = None
    error:           str | None = None
    component:       str = ""
    expect_desc:     str = ""
    timeout_s:       float = 10.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id, "name": self.name, "status": self.status.value,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "duration_s": (round(self.finished_at - self.started_at, 2)
                           if self.started_at and self.finished_at else None),
            "observations": [o.as_dict() for o in self.observations],
            "matched": self.matched.as_dict() if self.matched else None,
            "error": self.error, "component": self.component,
            "expect_desc": self.expect_desc, "timeout_s": self.timeout_s,
        }


@dataclass
class Stage:
    """Definition of one pipeline stage (not yet run).

    Attributes:
        stage_id: Unique stage identifier.
        name: Human-readable stage name.
        component: Subsystem this stage represents.
        expect_desc: Human description of what is expected.
        subscribe_topics: MQTT topic filters to watch during this stage.
        match_fn: Called with (topic, payload, ctx); returns True if the
            observed MQTT message satisfies this stage.
        timeout_s: Maximum seconds to wait for a match.
        on_enter: Called when the stage starts; receives ctx and may return
            extra context keys.
        on_skip: Called when the stage is skipped; receives ctx and may
            return extra context keys.
        extract_ctx: Called on match with (topic, payload, ctx); may return
            extra context keys.
        active_check: Optional active poll callable (sync or async) run
            alongside passive MQTT matching.
        active_check_interval_s: Seconds between active check polls.
    """

    stage_id:    str
    name:        str
    component:   str
    expect_desc: str
    subscribe_topics: list[str]
    match_fn:    Callable[[str, dict[str, Any], dict[str, Any]], bool]
    timeout_s:   float = 10.0
    on_enter:    Callable[[dict[str, Any]], Any] | None = None
    on_skip:     Callable[[dict[str, Any]], Any] | None = None
    extract_ctx: Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None
    active_check: Callable[[dict[str, Any]], Any] | None = None
    active_check_interval_s: float = 1.0


@dataclass
class Scenario:
    """Collection of stages defining one end-to-end flow."""

    scenario_id: str
    title:       str
    description: str
    stages:      list[Stage]


@dataclass
class RunReport:
    """Result of executing a scenario."""

    run_id:      str
    scenario_id: str
    status:      RunStatus = RunStatus.PENDING
    started_at:  float | None = None
    finished_at: float | None = None
    stage_results: list[StageResult] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    context:     dict[str, Any] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "scenario_id": self.scenario_id,
            "status": self.status.value,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "stage_results": [s.as_dict() for s in self.stage_results],
            "context": self.context,
        }


class ScenarioRunner:
    """Drives one Scenario through to completion (or abort), publishing
    live state via an asyncio callback so the UI can stream progress.
    """


    def __init__(
        self,
        scenario: Scenario,
        mqtt_subscribe: Callable[[str], None],
        mqtt_unsubscribe: Callable[[str], None],
        mqtt_publish: Callable[[str, Any, int, bool], None],
        register_listener: Callable[[Callable[[str, Any, int, bool], None]], Callable[[], None]],
        on_update: Callable[[RunReport], Any],
    ) -> None:
        self.scenario = scenario
        self._sub      = mqtt_subscribe
        self._unsub    = mqtt_unsubscribe
        self._pub      = mqtt_publish
        self._register_listener = register_listener
        self._on_update = on_update

        self.report = RunReport(
            run_id=str(uuid.uuid4())[:8],
            scenario_id=scenario.scenario_id,
        )
        self._abort_requested = False
        self._skip_requested: str | None = None
        self._current_stage_event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    # Public controls (called from API)

    def request_skip(self, stage_id: str) -> None:
        """Request that the currently running stage be skipped."""
        self._skip_requested = stage_id
        self._current_stage_event.set()

    def request_abort(self) -> None:
        """Request the entire run be aborted."""
        self._abort_requested = True
        self._current_stage_event.set()

    # Execution

    async def run(self) -> RunReport:
        """Execute all stages of the scenario and return the final report."""
        self._loop = asyncio.get_running_loop()
        self.report.status = RunStatus.RUNNING
        self.report.started_at = time.time()
        await self._emit()

        for stage_def in self.scenario.stages:
            if self._abort_requested:
                self.report.status = RunStatus.ABORTED
                break

            result = await self._run_stage(stage_def)
            self.report.stage_results.append(result)
            await self._emit()

            if self._abort_requested:
                self.report.status = RunStatus.ABORTED
                break

        if not self._abort_requested:
            any_failed = any(
                r.status in (StageStatus.FAILED, StageStatus.TIMEOUT)
                for r in self.report.stage_results
            )
            self.report.status = RunStatus.FAILED if any_failed else RunStatus.PASSED

        self.report.finished_at = time.time()
        await self._emit()
        return self.report

    async def _run_stage(self, stage_def: Stage) -> StageResult:
        """Execute a single stage and return its result."""
        result = StageResult(
            stage_id=stage_def.stage_id, name=stage_def.name,
            component=stage_def.component, expect_desc=stage_def.expect_desc,
            timeout_s=stage_def.timeout_s, status=StageStatus.RUNNING,
            started_at=time.time(),
        )
        self.report.stage_results.append(result)
        self.report.stage_results.pop()
        await self._emit()

        self._skip_requested = None
        self._current_stage_event = asyncio.Event()

        loop_done = asyncio.Event()
        matched_holder: dict[str, Any] = {}

        def _listener(topic: str, payload: Any, qos: int, retain: bool) -> None:
            if not _topic_matches_any(topic, stage_def.subscribe_topics):
                return
            obs = MQTTObservation(topic=topic, payload=payload, qos=qos, retain=retain)
            result.observations.append(obs)
            try:
                ok = stage_def.match_fn(topic, payload, self.report.context)
            except Exception as exc:
                ok = False
                result.error = f"match_fn error: {exc}"
            if ok and "matched" not in matched_holder:
                matched_holder["matched"] = obs
                self._loop.call_soon_threadsafe(loop_done.set)  # pyright: ignore[reportOptionalMemberAccess]

        unregister = self._register_listener(_listener)
        for t in stage_def.subscribe_topics:
            self._sub(t)

        try:
            if stage_def.on_enter:
                try:
                    extra_ctx = stage_def.on_enter(self.report.context)
                    if extra_ctx:
                        self.report.context.update(extra_ctx)
                    self._flush_pending_publish()
                except Exception as exc:
                    result.error = f"on_enter error: {exc}"

            timeout_task = asyncio.create_task(asyncio.sleep(stage_def.timeout_s))
            match_task   = asyncio.create_task(loop_done.wait())
            control_task = asyncio.create_task(self._current_stage_event.wait())
            tasks: set[asyncio.Task[Any]] = {timeout_task, match_task, control_task}

            poll_task: asyncio.Task[None] | None = None
            if stage_def.active_check:
                poll_task = asyncio.create_task(
                    self._poll_active_check(stage_def, loop_done, matched_holder)
                )
                tasks.add(poll_task)

            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()

            if self._abort_requested:
                result.status = StageStatus.SKIPPED
                result.error  = "Run aborted by operator"
            elif self._skip_requested == stage_def.stage_id:  # pyright: ignore[reportUnnecessaryComparison]
                result.status = StageStatus.SKIPPED
                result.error  = "Manually skipped by operator"
                if stage_def.on_skip:
                    try:
                        extra_ctx = stage_def.on_skip(self.report.context)
                        if extra_ctx:
                            self.report.context.update(extra_ctx)
                        self._flush_pending_publish()
                    except Exception as exc:
                        result.error += f" (on_skip error: {exc})"
            elif "matched" in matched_holder:
                obs = matched_holder["matched"]
                result.matched = obs
                result.status  = StageStatus.PASSED
                if stage_def.extract_ctx:
                    try:
                        extra_ctx = stage_def.extract_ctx(obs.topic, obs.payload, self.report.context)
                        if extra_ctx:
                            self.report.context.update(extra_ctx)
                    except Exception as exc:
                        result.error = f"extract_ctx error: {exc}"
            else:
                result.status = StageStatus.TIMEOUT
                result.error  = f"No matching message within {stage_def.timeout_s}s"
                if stage_def.on_skip:
                    try:
                        extra_ctx = stage_def.on_skip(self.report.context)
                        if extra_ctx:
                            self.report.context.update(extra_ctx)
                        self._flush_pending_publish()
                    except Exception as exc:
                        result.error += f" (on_skip error: {exc})"
        finally:
            unregister()
            for t in stage_def.subscribe_topics:
                self._unsub(t)

        result.finished_at = time.time()
        return result

    async def _emit(self) -> None:
        """Notify the on_update callback of the current report state."""
        res = self._on_update(self.report)
        if asyncio.iscoroutine(res):
            await res

    def _flush_pending_publish(self) -> None:
        """Publish any messages stashed in ctx['_publish_now'] by on_enter/on_skip."""
        pending = self.report.context.pop("_publish_now", None)
        if pending:
            items: list[Any] = pending if isinstance(pending, list) else [pending]  # pyright: ignore[reportUnknownVariableType]
            for topic, payload, qos, retain in items:
                self._pub(topic, payload, qos, retain)

    async def _poll_active_check(
        self, stage_def: Stage, loop_done: asyncio.Event, matched_holder: dict[str, Any],
    ) -> None:
        """Repeatedly call stage_def.active_check(ctx) until it returns True
        or the stage's overall timeout fires."""
        while True:
            try:
                result = stage_def.active_check(self.report.context)  # pyright: ignore[reportOptionalCall]
                if asyncio.iscoroutine(result):
                    result = await result
                if result:
                    if "matched" not in matched_holder:
                        matched_holder["matched"] = MQTTObservation(
                            topic="(active-check)", payload={"ok": True},
                            qos=0, retain=False,
                        )
                    loop_done.set()
                    return
            except Exception as exc:
                print(f"[ScenarioRunner] active_check error: {exc}")
            await asyncio.sleep(stage_def.active_check_interval_s)


def _topic_matches_any(topic: str, filters: list[str]) -> bool:
    for f in filters:
        if _mqtt_topic_match(topic, f):
            return True
    return False


def _mqtt_topic_match(topic: str, filt: str) -> bool:
    """Minimal MQTT topic-filter matcher supporting + and # wildcards."""
    if filt == topic:
        return True
    t_parts = topic.split("/")
    f_parts = filt.split("/")
    i = 0
    while i < len(f_parts):
        if f_parts[i] == "#":
            return True
        if i >= len(t_parts):
            return False
        if f_parts[i] != "+" and f_parts[i] != t_parts[i]:
            return False
        i += 1
    return i == len(t_parts)
