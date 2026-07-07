#!/usr/bin/env python3
"""
E2E Scenario Runner — Docker Compose lifecycle + Orchestrator API + Reporting.
"""

import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import requests

REPORTS_DIR = Path(__file__).parent
COMPOSE_FILE = Path(__file__).resolve().parent.parent / "Infrastructure_Repo" / "docker-compose.yml"
ORCHESTRATOR_URL = "http://localhost:9090"

SCENARIOS = [
    {"id": "fire_dispatch",         "title": "1. Fire Dispatch End-to-End"},
    {"id": "telemetry_aggregation", "title": "2. Telemetry Aggregation & Dashboard"},
    {"id": "leader_election",       "title": "3. Leader Election Failover"},
    {"id": "approval_gate",         "title": "4. Human Approval Gate"},
    {"id": "node_lifecycle",        "title": "5. Node Lifecycle & LWT Crash Detection"},
]

POLL_INTERVAL = 2.0
MAX_WAIT_ORCHESTRATOR = 180.0
MAX_WAIT_RUN = 300.0
COMPOSE_UP_TIMEOUT = 300.0


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def run_compose(*args, timeout: float = 120.0) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE)] + list(args)
    log(f"Running: {' '.join(cmd)}")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def wait_for_orchestrator(timeout: float = MAX_WAIT_ORCHESTRATOR) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{ORCHESTRATOR_URL}/api/scenarios", timeout=3.0)
            if r.status_code == 200:
                log("Orchestrator is ready")
                return True
        except requests.RequestException as e:
            log(f"  Waiting for orchestrator... ({type(e).__name__})")
            pass
        time.sleep(2.0)
    return False


def wait_for_run_complete(run_id: str, timeout: float = MAX_WAIT_RUN) -> dict:
    start = time.time()
    last_status = None
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{ORCHESTRATOR_URL}/api/runs/{run_id}", timeout=3.0)
            if r.status_code == 200:
                report = r.json()
                status = report.get("status", "UNKNOWN")
                if status != last_status:
                    log(f"  Run status: {status}")
                    last_status = status
                if status in ("PASSED", "FAILED", "ABORTED"):
                    return report
        except requests.RequestException:
            pass
        time.sleep(POLL_INTERVAL)
    return {"error": f"Timed out waiting for run {run_id} after {timeout}s", "status": "TIMEOUT"}


def get_transcript(limit: int = 500) -> list:
    try:
        r = requests.get(f"{ORCHESTRATOR_URL}/api/transcript?limit={limit}", timeout=3.0)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return []


def generate_report(
    scenario_id: str,
    title: str,
    run_result: dict,
    docker_logs: dict[str, str],
    transcript: list,
    build_duration: float,
    run_duration: float,
) -> str:
    lines = []
    lines.append(f"{'='*80}")
    lines.append(f"  E2E TEST REPORT: {title}")
    lines.append(f"{'='*80}")
    lines.append(f"  Scenario ID:   {scenario_id}")
    lines.append(f"  Run ID:        {run_result.get('run_id', 'N/A')}")
    lines.append(f"  Overall Status: {run_result.get('status', 'UNKNOWN')}")
    lines.append(f"  Build Time:    {build_duration:.1f}s")
    lines.append(f"  Run Time:      {run_duration:.1f}s")
    lines.append(f"  Timestamp:     {datetime.now().isoformat()}")
    lines.append("")

    stage_results = run_result.get("stage_results", [])
    lines.append(f"{'-'*60}")
    lines.append(f"  STAGE RESULTS ({len(stage_results)} stages)")
    lines.append(f"{'-'*60}")
    lines.append("")

    for sr in stage_results:
        stage_id = sr.get("stage_id", "?")
        status = sr.get("status", "?")
        name = sr.get("name", "")
        component = sr.get("component", "")
        error = sr.get("error")
        duration = sr.get("duration_s")

        lines.append(f"  [{status:7s}] {stage_id}")
        lines.append(f"           {name}")
        lines.append(f"           Component: {component}")
        if duration is not None:
            lines.append(f"           Duration:  {duration:.1f}s")
        if error:
            lines.append(f"           ERROR:     {error}")
        lines.append("")

    if transcript:
        lines.append(f"{'-'*60}")
        lines.append(f"  TRANSCRIPT ({len(transcript)} messages, showing first 20)")
        lines.append(f"{'-'*60}")
        lines.append("")
        for i, msg in enumerate(transcript[:20]):
            topic = msg.get("topic", "?")
            payload = msg.get("payload", {})
            lines.append(f"  [{i:3d}] {topic}")
            if isinstance(payload, dict):
                for pl in json.dumps(payload, indent=2).split("\n")[:4]:
                    lines.append(f"          {pl}")
            else:
                lines.append(f"          {str(payload)[:100]}")
            lines.append("")
        if len(transcript) > 20:
            lines.append(f"  ... ({len(transcript) - 20} more messages)")
            lines.append("")

    lines.append(f"{'-'*60}")
    lines.append(f"  DOCKER CONTAINER LOGS (last 30 lines each)")
    lines.append(f"{'-'*60}")
    lines.append("")
    for container, log_text in docker_logs.items():
        lines.append(f"  --- {container} ---")
        for l in log_text.strip().split("\n")[-30:]:
            lines.append(f"  | {l}")
        lines.append("")

    run_context = run_result.get("context", {})
    if run_context:
        lines.append(f"{'-'*60}")
        lines.append(f"  RUN CONTEXT")
        lines.append(f"{'-'*60}")
        lines.append(json.dumps(run_context, indent=2))
        lines.append("")

    lines.append(f"{'='*80}")
    lines.append("")
    return "\n".join(lines)


def capture_docker_logs() -> dict[str, str]:
    containers = [
        "wfc-mosquitto", "wfc-central", "wfc-backup",
        "sl-A-01", "sl-A-02", "sd-A-01", "fd-A-01",
        "wfc-dashboard", "wfc-test-orchestrator",
    ]
    logs = {}
    for c in containers:
        try:
            r = subprocess.run(
                ["docker", "logs", c, "--tail", "100"],
                capture_output=True, text=True, timeout=10.0,
            )
            logs[c] = (r.stdout or "") + (r.stderr or "")
        except Exception:
            logs[c] = "(unable to capture)"
    return logs


def run_scenario(scenario: dict) -> dict:
    sid = scenario["id"]
    title = scenario["title"]
    report_path = REPORTS_DIR / f"report_{sid}.txt"

    log(f"\n{'#'*70}")
    log(f"# SCENARIO: {title}")
    log(f"{'#'*70}\n")

    # 1. Clean up
    log("Step 1: docker compose down -v (clean slate)")
    try:
        run_compose("down", "-v", timeout=30.0)
    except subprocess.TimeoutExpired:
        log("  (down -v timed out)")
    time.sleep(3.0)

    # 2. Build and start
    log("Step 2: docker compose up --build -d")
    t0 = time.time()
    try:
        result = run_compose("up", "--build", "-d", timeout=COMPOSE_UP_TIMEOUT)
        if result.returncode != 0:
            log(f"  WARNING: compose up returned {result.returncode}")
    except subprocess.TimeoutExpired:
        log(f"  WARNING: compose up timed out after {COMPOSE_UP_TIMEOUT}s, checking status")
    build_duration = time.time() - t0

    # 3. Wait for orchestrator
    log("Step 3: Wait for orchestrator API")
    if not wait_for_orchestrator():
        log("  ERROR: Orchestrator did not become ready within timeout")
        docker_logs = capture_docker_logs()
        report = generate_report(
            sid, title,
            {"run_id": "N/A", "status": "INFRASTRUCTURE_FAILURE", "stage_results": []},
            docker_logs, [], build_duration, 0.0,
        )
        report_path.write_text(report, encoding="utf-8")
        log(f"  Report written to {report_path}")
        try:
            run_compose("down", "-v", timeout=30.0)
        except Exception:
            pass
        return {"scenario": sid, "status": "INFRASTRUCTURE_FAILURE"}

    # 4. Start the scenario run via orchestrator API
    log(f"Step 4: POST /api/runs/start  scenario_id={sid}")
    t1 = time.time()
    try:
        r = requests.post(
            f"{ORCHESTRATOR_URL}/api/runs/start",
            json={"scenario_id": sid},
            timeout=10.0,
        )
        if r.status_code != 200:
            log(f"  ERROR: Start run returned {r.status_code}: {r.text[:300]}")
            docker_logs = capture_docker_logs()
            report = generate_report(
                sid, title,
                {"run_id": "N/A", "status": f"START_FAILED ({r.status_code})", "stage_results": []},
                docker_logs, [], build_duration, 0.0,
            )
            report_path.write_text(report, encoding="utf-8")
            try:
                run_compose("down", "-v", timeout=30.0)
            except Exception:
                pass
            return {"scenario": sid, "status": "START_FAILED"}

        run_data = r.json()
        run_id = run_data.get("run_id", "N/A")
        log(f"  Run started: {run_id}")
    except Exception as e:
        log(f"  ERROR posting start run: {e}")
        docker_logs = capture_docker_logs()
        report = generate_report(
            sid, title,
            {"run_id": "N/A", "status": f"START_FAILED ({e})", "stage_results": []},
            docker_logs, [], build_duration, 0.0,
        )
        report_path.write_text(report, encoding="utf-8")
        try:
            run_compose("down", "-v", timeout=30.0)
        except Exception:
            pass
        return {"scenario": sid, "status": "START_FAILED"}

    # 5a. For leader_election: kill the real leader so backup detects heartbeat timeout
    if sid == "leader_election":
        log("  Stopping sl-A-01 to trigger heartbeat timeout on backup...")
        try:
            subprocess.run(["docker", "stop", "sl-A-01"], timeout=15.0, capture_output=True)
            log("  sl-A-01 stopped")
        except Exception as e:
            log(f"  WARNING: could not stop sl-A-01: {e}")
        time.sleep(2.0)

    # 5b. Wait for completion
    log("Step 5: Wait for run to complete")
    run_result = wait_for_run_complete(run_id)
    run_duration = time.time() - t1

    # 6. Capture data
    log("Step 6: Capture transcript and docker logs")
    transcript = get_transcript()
    docker_logs = capture_docker_logs()

    # 7. Generate report
    log("Step 7: Generate report")
    report = generate_report(sid, title, run_result, docker_logs, transcript, build_duration, run_duration)
    report_path.write_text(report, encoding="utf-8")
    log(f"  Report written to {report_path}")

    # 8. Clean up
    log("Step 8: docker compose down -v")
    try:
        run_compose("down", "-v", timeout=30.0)
    except subprocess.TimeoutExpired:
        log("  (down -v timed out)")
    time.sleep(2.0)

    return {
        "scenario": sid,
        "title": title,
        "status": run_result.get("status", "UNKNOWN"),
        "run_id": run_id,
        "run_duration": round(run_duration, 1),
        "stage_count": len(run_result.get("stage_results", [])),
        "stages": [
            {
                "id": s.get("stage_id"),
                "status": s.get("status"),
                "error": s.get("error"),
                "duration_s": s.get("duration_s"),
            }
            for s in run_result.get("stage_results", [])
        ],
    }


def generate_summary(all_results: list[dict]):
    lines = []
    lines.append(f"{'='*80}")
    lines.append(f"  WFC E2E TEST SUITE — COMPREHENSIVE SUMMARY")
    lines.append(f"{'='*80}")
    lines.append(f"  Completed: {datetime.now().isoformat()}")
    lines.append("")

    passed = sum(1 for r in all_results if r.get("status") == "PASSED")
    failed = sum(1 for r in all_results if r.get("status") in ("FAILED", "START_FAILED", "INFRASTRUCTURE_FAILURE", "TIMEOUT"))
    total = len(all_results)

    lines.append(f"  Result: {passed}/{total} passed, {failed}/{total} failed")
    lines.append("")

    lines.append(f"{'-'*60}")
    lines.append(f"  PER-SCENARIO BREAKDOWN")
    lines.append(f"{'-'*60}")
    lines.append("")

    for r in all_results:
        sid = r.get("scenario", "?")
        title = r.get("title", "")
        status = r.get("status", "?")
        dur = r.get("run_duration", 0)
        lines.append(f"  [{status:20s}] {title}")
        lines.append(f"           ID: {sid}, Duration: {dur}s")
        stages = r.get("stages", [])
        for s in stages:
            sid_s = s.get("id", "?")
            st = s.get("status", "?")
            err = s.get("error")
            sd = s.get("duration_s")
            dur_str = f"{sd}s" if sd else "?"
            if err and err != "Manually skipped by operator":
                lines.append(f"           ├─ [{st:7s}] {sid_s} ({dur_str}) — {err}")
            else:
                lines.append(f"           ├─ [{st:7s}] {sid_s} ({dur_str})")
        lines.append("")

    # Collect code errors (not skip-related)
    all_errors = []
    for r in all_results:
        stages = r.get("stages", [])
        for s in stages:
            err = s.get("error")
            if err and not err.startswith("(on_skip") and "skip" not in err.lower() and err != "Manually skipped by operator":
                all_errors.append({
                    "scenario": r.get("title"),
                    "stage": s.get("id"),
                    "error": err,
                })

    if all_errors:
        lines.append(f"{'-'*60}")
        lines.append(f"  CODE ERRORS FOUND")
        lines.append(f"{'-'*60}")
        lines.append("")
        for e in all_errors:
            lines.append(f"  Scenario: {e['scenario']}")
            lines.append(f"  Stage:    {e['stage']}")
            lines.append(f"  Error:    {e['error']}")
            lines.append("")

    lines.append(f"{'='*80}")
    summary_text = "\n".join(lines)
    (REPORTS_DIR / "summary.txt").write_text(summary_text, encoding="utf-8")
    print(summary_text)
    return summary_text


def main():
    skip_scenarios = set()
    if "--skip" in sys.argv:
        for i, arg in enumerate(sys.argv):
            if arg == "--skip" and i + 1 < len(sys.argv):
                skip_scenarios.add(int(sys.argv[i + 1]) - 1)
    only_scenario = None
    if "--only" in sys.argv:
        idx = sys.argv.index("--only") + 1
        if idx < len(sys.argv):
            only_scenario = int(sys.argv[idx]) - 1

    all_results = []
    for i, scenario in enumerate(SCENARIOS):
        if only_scenario is not None and i != only_scenario:
            continue
        if i in skip_scenarios:
            log(f"Skipping scenario {i+1}: {scenario['title']}")
            continue
        try:
            result = run_scenario(scenario)
            all_results.append(result)
        except Exception as e:
            log(f"UNHANDLED ERROR in scenario {scenario['id']}: {e}")
            traceback.print_exc()
            all_results.append({
                "scenario": scenario["id"],
                "title": scenario["title"],
                "status": "SCRIPT_ERROR",
                "error": str(e),
            })
            try:
                run_compose("down", "-v", timeout=30.0)
            except Exception:
                pass

    if all_results:
        generate_summary(all_results)
    else:
        log("No scenarios were run.")


if __name__ == "__main__":
    main()
