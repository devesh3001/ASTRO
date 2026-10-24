"""Agents: the model client, two offline mock models, and the baselines."""
import json
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str          # raw text exactly as the model sent it
    extra_content: dict = None

    def parse(self):
        """Return (args, error). `args` is a dict when the arguments are valid JSON."""
        try:
            args = json.loads(self.arguments) if self.arguments else {}
        except (json.JSONDecodeError, TypeError) as exc:
            return None, f"arguments are not valid JSON ({exc})"
        if not isinstance(args, dict):
            return None, "arguments must be a JSON object"
        return args, None

    def history_arguments(self):
        """Arguments as stored in the conversation. Invalid JSON is replaced with {} so
        the next request is still accepted by every provider."""
        args, error = self.parse()
        return json.dumps(args) if error is None else "{}"


@dataclass
class Reply:
    text: str | None
    tool_calls: list = field(default_factory=list)
    raw: str = ""
    tokens: int = 0
    error: str | None = None


# ------------------------------------------------------------ real models
class OpenAIClient:
    """Calls any model through an OpenAI-compatible endpoint.

    Judges and teams are given a base_url, an api_key and a model name; the harness reads
    them from the environment variables BASE_URL, API_KEY and MODEL. The same call is used
    for every model, so switching models only means changing those values.
    """

    def __init__(self, model, api_key, base_url=None, max_tokens=None):
        self.model, self.api_key, self.base_url, self.max_tokens = model, api_key, base_url, max_tokens

    def start(self, task):
        pass

    @staticmethod
    def normalize(raw):
        try:
            data = json.loads(raw)
            message = data["choices"][0]["message"]
            text = message.get("content")
            if text is not None and not isinstance(text, str):
                raise ValueError("assistant content must be text")
            calls, ids = [], set()
            for index, call in enumerate(message.get("tool_calls") or []):
                fn = call.get("function", {})
                name = fn.get("name")
                if not isinstance(name, str) or not name:
                    raise ValueError("tool call missing function name")
                call_id = call.get("id")
                if not isinstance(call_id, str) or not call_id or call_id in ids:
                    call_id = f"normalized_{index}"
                ids.add(call_id)
                calls.append(ToolCall(call_id, name, fn.get("arguments", ""), call.get("extra_content")))
            return Reply(text, calls, raw, (data.get("usage") or {}).get("total_tokens", 0) or 0)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            return Reply(None, raw=raw, error=f"Unreadable model reply: {exc}. Please call a supported tool.")

    def complete(self, messages, tools, timeout):
        import subprocess, sys
        request = dict(model=self.model, messages=messages, tools=tools, api_key=self.api_key,
                       base_url=self.base_url, max_tokens=self.max_tokens, timeout=timeout)
        # A separate process makes the complete API operation cancellable, including retries.
        try:
            result = subprocess.run([sys.executable, "-m", "harness.provider_worker"],
                                    input=json.dumps(request), capture_output=True, text=True,
                                    encoding="utf-8", timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("model request exceeded its total deadline") from exc
        if result.returncode:
            raise RuntimeError("model provider request failed; check endpoint, model and credentials")
        return self.normalize(result.stdout)


# ------------------------------------------------------------ mock models
class MockClient:
    """Offline stand-ins so the environment runs with no API key.

    mock/solver  writes the reference solution, then finishes.
    mock/idle    finishes immediately without doing anything.
    """

    def __init__(self, kind):
        self.kind = kind
        self.turn = 0
        self.task = None

    def start(self, task):
        self.task, self.turn = task, 0

    def complete(self, messages, tools, timeout):
        self.turn += 1
        calls = []
        if self.kind == "solver" and self.turn == 1:
            for path, content in self.task.reference.items():
                calls.append(("write_file", {"path": path, "content": content}))
            calls.append(("run_python", {"code": "print('verified')"}))
        else:
            calls.append(("finish", {}))
        tool_calls = [ToolCall(f"mock-{self.turn}-{i}", name, json.dumps(args))
                      for i, (name, args) in enumerate(calls)]
        return Reply(text=None, tool_calls=tool_calls, raw=json.dumps({"mock": self.kind, "turn": self.turn}))


def make_client(model, api_key, base_url, max_tokens=None):
    if model.startswith("mock/"):
        kind = model.split("/", 1)[1]
        if kind not in ("solver", "idle"):
            raise ValueError("mock models are 'mock/solver' and 'mock/idle'")
        return MockClient(kind)
    if not api_key:
        raise ValueError("API_KEY is not set")
    return OpenAIClient(model, api_key, base_url, max_tokens)


# -------------------------------------------------------------- baselines
def oracle_actions(task):
    """The reference solution as a list of (tool, args) actions. Must score full marks."""
    return [("write_file", {"path": path, "content": content}) for path, content in task.reference.items()]


def noop_actions(task):
    """An agent that gives up immediately. Must score about zero."""
    return []
