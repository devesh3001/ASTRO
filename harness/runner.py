"""Episode loop, baseline runner, and telemetry."""
import copy
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .env import PASS_SCORE
from .tools import SPECS, TOOLS, TOOL_NAMES, check_args

SYSTEM_PROMPT = (
    "You are an autonomous satellite fault-response system working on a CubeSat's onboard computer. "
    "A subsystem anomaly has been detected. Use the provided tools to read config files and "
    "telemetry logs, diagnose the fault, apply corrective parameter fixes, and verify your changes "
    "by running Python checks. Always respond with tool calls. "
    "When all faults are resolved and the satellite is back to nominal, call `finish`."
)
TRIMMED = "[older tool output removed to save context]"
TRIMMED_ARGS = '{"note": "arguments removed to save context"}'


@dataclass
class Limits:
    max_steps: int = 40            # one step = one model reply
    max_seconds: int = 600         # clock starts at the first model request
    max_tool_calls: int = 10       # per reply; extra calls are not run
    context_budget_tokens: int = 48000
    max_output_chars: int = 4000


class JsonlLog:
    def __init__(self, path):
        self.file = open(path, "a", buffering=1, encoding="utf-8")

    def write(self, record):
        self.file.write(json.dumps(record, default=str) + "\n")

    def close(self):
        self.file.close()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def show(record):
    """One readable line per step on the terminal (live telemetry)."""
    tools = ",".join(a.get("tool", "?") for a in record["parsed_actions"]) or "-"
    reward = record["reward"].get("total") if record.get("reward") else None
    print(f"[{record['agent']:<6} step {record['step']:>2}] tools={tools:<40} "
          f"reward={reward if reward is not None else '-':<6} state={record['state']['hash']} "
          f"errors={len(record['errors'])}", flush=True)


# --------------------------------------------------------- context trimming
def estimate_tokens(messages):
    # UTF-8 byte count is deliberately conservative for byte-based tokenizers.
    return len(json.dumps(messages, ensure_ascii=False, default=str).encode("utf-8"))


def trim_history(messages, budget):
    while estimate_tokens(messages) > budget:
        turns = [i for i, m in enumerate(messages) if m["role"] == "assistant"]
        if len(turns) > 1:
            del messages[turns[0]:turns[1]]  # remove whole tool-call/result groups
            continue
        victim = next((m for m in messages[2:] if isinstance(m.get("content"), str)
                       and len(m["content"]) > 128), None)
        if victim is not None:
            victim["content"] = TRIMMED
            continue
        shrunk = False
        for message in messages[2:]:
            for call in message.get("tool_calls", []):
                if call["function"]["arguments"] != TRIMMED_ARGS:
                    call["function"]["arguments"] = TRIMMED_ARGS
                    shrunk = True
        if not shrunk:
            raise ValueError("system/task instructions and tool schema exceed context budget")


# ------------------------------------------------------------ baselines
def diagnostics(env, record):
    try:
        record["state"] = {"hash": env.state_hash()}
        record["reward"] = env.score()
        record["verifier"] = dict(getattr(env, "last_verifier", {}))
    except Exception as exc:
        record["errors"].append({"kind": "diagnostics_failed", "detail": f"{type(exc).__name__}: {exc}"})
        return False
    return True


def run_scripted(env, task_id, seed, actions, agent, log):
    step, final, termination = 0, {"total": 0.0}, "crash"
    try:
        env.reset(task_id, seed)
        for step, (name, args) in enumerate(actions, start=1):
            text, is_error = env.step(name, args)
            record = {"timestamp": now(), "agent": agent, "task": task_id, "step": step, "seed": seed,
                      "model_input": None, "model_output_raw": None,
                      "parsed_actions": [{"tool": name, "args": args}], "tool_result": [text],
                      "errors": [{"kind": "tool_error", "detail": text}] if is_error else [],
                      "state": {"hash": None}, "reward": None, "tokens": 0, "latency_ms": 0}
            ok = diagnostics(env, record)
            log.write(record)
            show(record)
            if not ok or is_error:
                raise RuntimeError(text if is_error else "baseline diagnostics failed")
        final = env.score()
        termination = "success" if final["total"] >= PASS_SCORE else "failure"
        return final
    finally:
        log.write({"timestamp": now(), "agent": agent, "task": task_id, "event": "episode_end",
                   "seed": seed, "steps": step, "termination": termination, "reward": final,
                   "verifier": dict(getattr(env, "last_verifier", {}))})


def run_episode(env, task_id, seed, client, append_text, limits, log):
    started, deadline, termination, steps = None, None, None, 0
    final, fatal = {"total": None}, None
    messages = []
    try:
        prompt = env.reset(task_id, seed)
        client.start(env.task)
        system = SYSTEM_PROMPT + (("\n\n" + append_text) if append_text else "")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        finished = False
        while steps < limits.max_steps and not finished:
            trim_history(messages, limits.context_budget_tokens - estimate_tokens(TOOLS) - 4096)
            if started is None:
                started = time.monotonic()
                deadline = started + limits.max_seconds
            if time.monotonic() >= deadline:
                termination = "time_cap"
                break
            sent = copy.deepcopy(messages)
            call_start = time.monotonic()
            env.deadline = deadline
            try:
                reply = client.complete(sent, TOOLS, timeout=min(60.0, deadline - call_start))
            except Exception as exc:
                log.write({"timestamp": now(), "agent": "model", "task": task_id,
                           "event": "model_call_failed", "seed": seed, "steps": steps,
                           "error": f"{type(exc).__name__}: {exc}"})
                termination = "time_cap" if time.monotonic() >= deadline else "crash"
                break
            steps += 1  # exactly one step per received reply, including malformed replies
            record = {"timestamp": now(), "agent": "model", "task": task_id, "step": steps, "seed": seed,
                      "model_input": sent, "model_output_raw": reply.raw,
                      "parsed_actions": [], "tool_result": [], "errors": [],
                      "state": {"hash": None}, "reward": None, "tokens": reply.tokens,
                      "latency_ms": int((time.monotonic() - call_start) * 1000)}
            assistant = {"role": "assistant", "content": reply.text}
            if reply.tool_calls:
                assistant["tool_calls"] = []
                for call in reply.tool_calls:
                    item = {"id": call.id, "type": "function",
                            "function": {"name": call.name, "arguments": call.history_arguments()}}
                    if call.extra_content:
                        item["extra_content"] = call.extra_content
                    assistant["tool_calls"].append(item)
            messages.append(assistant)
            if not reply.tool_calls:
                detail = getattr(reply, "error", None) or "No tool call received; call a tool or finish."
                record["errors"].append({"kind": "unreadable_reply" if getattr(reply, "error", None) else "no_tool_call", "detail": detail})
                messages.append({"role": "user", "content": detail})
            for index, call in enumerate(reply.tool_calls):
                args, parse_error = call.parse()
                record["parsed_actions"].append({"tool": call.name, "args": args, "parse_error": parse_error})
                error_kind = None
                if time.monotonic() >= deadline:
                    result, error_kind = "not run: episode deadline exceeded", "time_cap"
                    termination, finished = "time_cap", True
                elif finished:
                    result = "not run: episode already finished"
                elif index >= limits.max_tool_calls:
                    result, error_kind = "not run: at most 10 tool calls allowed per reply", "too_many_tool_calls"
                elif parse_error:
                    result, error_kind = "error: " + parse_error, "unparsable_arguments"
                elif call.name not in SPECS:
                    result, error_kind = "error: unknown tool; available: " + ", ".join(TOOL_NAMES), "unknown_tool"
                elif (problem := check_args(call.name, args)):
                    result, error_kind = "error: " + problem, "invalid_arguments"
                elif call.name == "finish":
                    finished, result = True, "Episode finished."
                else:
                    try:
                        result, is_error = env.step(call.name, args)
                        if is_error:
                            error_kind = "tool_error"
                    except Exception as exc:
                        result, error_kind = f"error: {type(exc).__name__}: {exc}", "tool_error"
                result = str(result)[:limits.max_output_chars]
                if error_kind:
                    record["errors"].append({"kind": error_kind, "detail": result})
                record["tool_result"].append(result)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
            if time.monotonic() >= deadline:
                termination, finished = "time_cap", True
            else:
                diagnostics(env, record)
            log.write(record)
            show(record)
        if termination is None and not finished:
            termination = "step_cap"
    except Exception as exc:
        termination, fatal = "crash", f"{type(exc).__name__}: {exc}"
    finally:
        # Final verification has its own 120-second budget after model execution.
        env.deadline = None
        try:
            final = env.score()
        except Exception as exc:
            final, termination = {"total": None, "error": f"{type(exc).__name__}: {exc}"}, "crash"
        termination = termination or ("success" if (final.get("total") or 0) >= PASS_SCORE else "failure")
        elapsed = round(time.monotonic() - started, 3) if started else 0
        ending = {"timestamp": now(), "agent": "model", "task": task_id, "event": "episode_end",
                  "seed": seed, "steps": steps, "termination": termination, "reward": final,
                  "elapsed_s": elapsed, "error": fatal, "verifier": dict(getattr(env, "last_verifier", {}))}
        log.write(ending)
    return {key: ending[key] for key in ("termination", "steps", "reward", "elapsed_s", "verifier")}
