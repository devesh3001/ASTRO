"""Tool definitions shown to the model, plus argument validation."""

TOOLS = [
    {"type": "function", "function": {
        "name": "list_files",
        "description": "List all files in the satellite's onboard filesystem.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a config file or telemetry log from the onboard filesystem.",
        "parameters": {"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string",
                     "description": "Path relative to the onboard filesystem root."}}}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Write a corrected config file or script to the onboard filesystem.",
        "parameters": {"type": "object", "required": ["path", "content"], "properties": {
            "path": {"type": "string",
                     "description": "Path relative to the onboard filesystem root."},
            "content": {"type": "string",
                        "description": "Full new contents of the file."}}}}},
    {"type": "function", "function": {
        "name": "step_orbit",
        "description": "Advance satellite orbital simulation by a given duration (1-30 minutes) to observe dynamic telemetry consequences of current subsystem configurations.",
        "parameters": {"type": "object", "properties": {
            "minutes": {"type": "integer",
                        "description": "Number of orbital minutes to advance (default 10, between 1 and 30)."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "run_python",
        "description": "Run a Python snippet in the onboard filesystem to test a fix or inspect state.",
        "parameters": {"type": "object", "required": ["code"], "properties": {
            "code": {"type": "string",
                     "description": "Python source code to run."}}}}},
    {"type": "function", "function": {
        "name": "finish",
        "description": "Call this when all subsystem faults are resolved and the satellite is nominal.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
]

SPECS = {t["function"]["name"]: t["function"]["parameters"] for t in TOOLS}
TOOL_NAMES = list(SPECS)


def check_args(name, args):
    """Return an error message if args don't match the tool's schema, else None."""
    if not isinstance(args, dict):
        return "arguments must be a JSON object"
    spec = SPECS[name]
    for key in spec.get("required", []):
        if key not in args:
            return f"missing required argument '{key}'"
    for key, value in args.items():
        if key not in spec["properties"]:
            return f"unexpected argument '{key}'"
        expected_type = spec["properties"][key].get("type", "string")
        if expected_type == "integer":
            if not isinstance(value, int) and not (isinstance(value, str) and value.isdigit()):
                return f"argument '{key}' must be an integer"
        elif not isinstance(value, str):
            return f"argument '{key}' must be a string"
    return None
