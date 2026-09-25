# `jevq` implementation plan

## 1. Outcome

Implement the interface in `docs/spec.md` as a dependency-free Python 3.10+ command-line program, with deterministic unit and CLI-contract tests that do not contact TypeSafe.

Initial delivery consists of:

- `jevq.py`: executable implementation and importable functions;
- `test_jevq.py`: standard-library `unittest` test suite; and
- a short usage section in `README.md` once behavior is verified.

Packaging, automatic retries, endpoint overrides, and live integration tests remain follow-up work.

## 2. Program structure

Keep the runtime in one file while separating side effects behind small functions. The planned flow is:

```text
argv + environment + stdin
          |
          v
detect output mode -> parse/validate CLI -> read state -> build payload
                                                        |
                                                        v
                                              injectable HTTP call
                                                        |
                                                        v
                                           decode/validate response
                                                        |
                                                        v
                                            render result or error
```

Planned constants:

- `API_URL = "https://api.typesafe.ai/v1/systemone"`
- `DEFAULT_MODEL = "jev-latest"`
- `REQUEST_TIMEOUT_SECONDS = 30`
- `QUESTION_ID = "question"`
- a bounded maximum retained error-body size, initially 64 KiB

Planned functions and responsibilities:

| Function | Responsibility |
| --- | --- |
| `detect_json_mode(argv)` | Detect exact `-j`/`--json` flags before full parsing so parser failures use the requested format. |
| `make_parser()` | Define modes and arguments with `argparse`; override parser error handling so it never writes uncontrolled diagnostics. |
| `parse_cli(argv)` | Parse arguments and enforce cross-option rules such as the choice list consumed by `-c`/`--choices` and score-specific `--criteria`. |
| `parse_labels(value, option_name)` | Split, trim, and validate choices or score criteria while preserving order. |
| `read_state(text_parts, stdin)` | Apply positional-over-stdin precedence without accidentally blocking on a terminal. |
| `load_api_key(environ)` | Validate `TYPESAFE_API_KEY` without exposing its value. |
| `build_payload(config, state)` | Produce the exact `model`/`state`/`questions` request document. |
| `call_api(payload, api_key, opener)` | Serialize UTF-8 JSON, perform the POST with a 30-second timeout, and translate transport/HTTP failures. |
| `extract_answer(document, mode, labels)` | Validate the response envelope and typed answer before returning the reduced result. |
| `write_success(answer, mode, json_mode, stdout)` | Emit either the bare result or the documented compact JSON object. |
| `write_error(error, json_mode, stderr)` | Emit one sanitized plain-text or JSON diagnostic. |
| `main(argv, environ, stdin, stdout, stderr, opener)` | Orchestrate the command and return the documented exit status. |

Use a small internal exception or immutable error value carrying `code`, `message`, `exit_status`, and optional safe `details`. Expected failures travel through this path; unexpected exceptions are converted to `internal_error` with exit status 1 and without a traceback or exception representation.

`-h`/`--help` remains a successful human-readable operation on stdout. It is not an error and is not converted to JSON. All argument validation failures, including failures that occur before a mode is known, use the selected plain/JSON error contract.

## 3. CLI validation design

Use one required mutually exclusive group for `-c`/`--choices`, `-p`/`--probability`, `-n`/`--noul`, and `-s`/`--score`. The choice option consumes its comma-separated list directly. Both noul aliases set the same internal mode, while the group rejects supplying both aliases.

After parsing:

1. reject blank question or model values;
2. in choice mode, validate the list consumed by `-c`/`--choices` and reject `--criteria`;
3. in score mode, require `--criteria`;
4. in noul mode, reject `--criteria`;
5. validate label cardinality, duplicates, empty items, and ASCII control characters; and
6. obtain non-blank state from positional text or non-interactive stdin.

Positional text beginning with `-` can be supplied after the conventional `--` separator; no additional parsing convention is needed.

## 4. HTTP and error handling

Use `urllib.request.Request` and an injectable opener compatible with `urllib.request.urlopen`.

Catch `urllib.error.HTTPError` before `urllib.error.URLError`, because `HTTPError` is a subclass of `URLError` and must retain the API-error exit mapping.

Request behavior:

- serialize with `json.dumps(..., ensure_ascii=False).encode("utf-8")`;
- send `Authorization: Bearer ...` and `Content-Type: application/json`;
- use `POST` and the single 30-second timeout; and
- perform no automatic retries.

Failure mapping:

| Condition | Error code | Exit |
| --- | --- | --- |
| timeout | `timeout` | 4 |
| DNS, TLS, connection, or other `URLError` | `network_error` | 4 |
| HTTP 401/403 | `authentication_error` | 5 |
| HTTP 429 | `rate_limited` | 5 |
| other non-2xx HTTP status | `api_error` | 5 |
| invalid UTF-8 or JSON success body | `response_error` | 6 |
| unexpected response envelope or answer | `response_error` | 6 |

Do not include request headers, the API key, raw exception representations, or unbounded response bodies in diagnostics. For an HTTP error, retain at most 64 KiB and expose only a concise message plus bounded JSON error fields when they can be decoded safely.

## 5. Response validation

Treat booleans as invalid numeric values even though `bool` subclasses `int` in Python. All numbers must be finite.

Common validation:

- top-level response is an object;
- `answers` is an object containing `question`; and
- the answer is an object whose `type` equals the requested API type.

Mode-specific validation:

- **choice:** `choice` is one of the submitted labels; `confidence` is in `[0, 1]`; `probabilities` is an object containing numeric `[0, 1]` values for the submitted labels. Preserve the API's numbers without rounding.
- **noul:** `noul` is numeric and in `[0, 1]`.
- **score:** `score` is finite and lies between `0` and `len(criteria) - 1`; require the documented `confidence`, `legend`, and `probabilities` fields to have their API-schema types even though reduced output only contains `score`.

Do not enforce exact probability sums because the API documents them as approximately one.

## 6. Test plan

Use `unittest`, `unittest.mock`, `io.StringIO`, and small fake response/opener objects. Call `main()` directly for most contract tests and test pure helpers separately. No default test may use a real API key or network connection.

### 6.1 Parsing and validation

- each mode and long/short alias;
- mutually exclusive mode failures, including `-p` plus `-n`;
- missing and blank question/model values;
- required choice operands and required/forbidden `--criteria` combinations;
- trimming, duplicate, empty, one-item, control-character, and order cases; and
- text beginning with `-` after `--`.

### 6.2 State and configuration

- positional words joined by one space;
- multiline stdin preserved exactly;
- positional text wins without reading redirected stdin;
- interactive stdin without positional state fails immediately;
- whitespace-only state fails; and
- missing, blank, and valid `TYPESAFE_API_KEY` values.

### 6.3 Request construction and transport

- exact choice, noul, and score payloads;
- default and overridden model;
- Unicode survives UTF-8 serialization;
- request method, URL, headers, and 30-second timeout;
- no retry after an opener failure; and
- key is absent from every error stream.

### 6.4 Responses and output

- plain and JSON success for all three modes;
- compact JSON plus a single trailing newline;
- parser, configuration, timeout, network, HTTP, decode, and schema failures in both output modes;
- 401/403, 429, and generic HTTP mappings;
- missing answer, wrong answer type, booleans-as-numbers, non-finite/out-of-range numbers, invalid choices, and malformed probability structures;
- no error output on stdout and no traceback; and
- `--help` succeeds on stdout.

Run the suite with:

```shell
python3 -m unittest -v
```

## 7. Implementation sequence

1. Create the error type, parser, early JSON-mode detection, and CLI validation helpers.
2. Implement state/key loading and request construction; cover them with pure unit tests.
3. Implement the injectable `urllib` transport and failure mapping; test with fake openers.
4. Implement strict response validation and plain/JSON rendering.
5. Wire `main()` and add end-to-end in-process CLI contract tests.
6. Run the complete test suite and manually exercise `--help` plus representative validation errors.
7. Add concise README usage derived from the verified behavior.
8. Review results before deciding on packaging, retries, endpoint overrides, or an opt-in live test.

## 8. Completion criteria

Implementation is ready for review when:

- every normative behavior and acceptance criterion in `docs/spec.md` is represented by code and a test;
- `python3 -m unittest -v` passes without network access;
- `python3 jevq.py --help` works on Python 3.10+;
- plain and JSON modes have no cross-stream output leakage;
- failures do not expose the API key, state, headers, or tracebacks;
- `git diff --check` passes; and
- deviations discovered during implementation are recorded in the spec before code review.

## 9. Planning blockers

None. The remaining packaging and post-testing reliability choices are explicitly deferred and do not affect the first implementation slice.
