# inputs/

Put extra instruction files here. Name them in `.env` with `APPEND_FILE=inputs/<filename>`.

Example:
    echo "Explain your plan in one line before each tool call." > inputs/extra.txt
    APPEND_FILE=inputs/extra.txt docker compose up --build ...
