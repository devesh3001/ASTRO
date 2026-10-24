# Extra instructions

Run A uses an empty `APPEND_FILE` and no `inputs/append.txt`. The checked-in sample is `append.example.txt`, which does not auto-load.

For Run B, save the exact judge-provided UTF-8 file here and set `APPEND_FILE=inputs/<filename>` in `.env`. Do not trim or rewrite its contents. A named missing, blank or unreadable file stops the run.

For compatibility, `inputs/append.txt` auto-loads when present and `APPEND_FILE` is empty. Keep that filename absent for an unappended demo. Try the optional sample with `APPEND_FILE=inputs/append.example.txt`.
