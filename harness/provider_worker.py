"""One cancellable API call. Credentials arrive on stdin, never command arguments."""
import json
import sys
import openai


def main():
    request = json.load(sys.stdin)
    client = openai.OpenAI(api_key=request.pop("api_key"), base_url=request.pop("base_url") or None,
                           max_retries=0)
    if not request.get("max_tokens"):
        request.pop("max_tokens", None)
    response = client.chat.completions.with_raw_response.create(**request)
    raw = response.text
    if len(raw.encode("utf-8")) > 1_000_000:
        raise ValueError("provider response exceeds output limit")
    sys.stdout.write(raw)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Provider errors sometimes echo request headers: do not log exception text.
        sys.stderr.write(type(exc).__name__)
        sys.exit(1)
