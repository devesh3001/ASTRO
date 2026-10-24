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
    # Stream the HTTP body into a bounded buffer, while requesting a normal
    # non-streaming model reply. Never buffer an unlimited provider body first.
    with client.chat.completions.with_streaming_response.create(**request) as response:
        chunks, size = [], 0
        for chunk in response.iter_bytes(chunk_size=8192):
            size += len(chunk)
            if size > 1_000_000:
                raise ValueError("provider response exceeds output limit")
            chunks.append(chunk)
        sys.stdout.write(b"".join(chunks).decode("utf-8"))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Provider errors sometimes echo request headers: do not log exception text.
        sys.stderr.write(type(exc).__name__)
        sys.exit(1)
