# `jevq` command-line client specification

## 1. Purpose

`jevq` is a small Python command-line client for Jev's TypeSafe-compatible
System One API. It submits one item of input state and one typed question, then
writes the relevant typed answer as plain text or, when requested, JSON. It can
call TypeSafe directly, route the compatible request through Vercel AI
Gateway, or call a local Jeff server.

The implementation lives in `jevq.py`, and invoking `python3 jevq.py ...` must work. Examples use `jevq` as the intended installed command name; the installation mechanism is deferred until after initial testing.

This version supports exactly one question per invocation and one of Jev's three question types:

- **choice**: select one member of a caller-supplied finite set;
- **noul**: estimate the probability that a yes/no proposition is true; or
- **score**: rate the state against an ordered, caller-supplied rubric.

Batching multiple questions in one API call, structured (non-string) state,
arbitrary/custom providers, and local caching are out of scope.

## 2. External API contract

Use the decision API selected by `--provider`:

| Provider | Endpoint | Default model | Credentials |
| --- | --- | --- | --- |
| `typesafe` | `POST https://api.typesafe.ai/v1/systemone` | `jev-latest` | `TYPESAFE_API_KEY` |
| `vercel` | `POST https://ai-gateway.vercel.sh/typesafe/v1/systemone` | `typesafe-ai/jev` | `AI_GATEWAY_API_KEY`, then `VERCEL_OIDC_TOKEN` |
| `jeff` | `POST http://localhost:8765/v1/systemone` | `jeff-latest` | optional `JEFF_API_KEY` |
| `chatgpt` | `POST https://api.openai.com/v1/decisions` | `gpt-6-luna` | `OPENAI_API_KEY` |

- authentication: `Authorization: Bearer <provider credential>` for TypeSafe,
  Vercel, and ChatGPT, and for Jeff only when `JEFF_API_KEY` is set
- content type: `application/json`
- model: the value of `--model`, or the selected provider's default
- question ID: `question`

The TypeSafe-compatible System One providers use this request shape:

```json
{
  "model": "jev-latest",
  "state": "input text",
  "questions": {
    "question": {
      "type": "noul",
      "instructions": "Does this ask for a refund?"
    }
  }
}
```

The System One question object changes by mode:

| Mode | API `type` | Additional request field |
| --- | --- | --- |
| choice | `choice` | `criteria`: an object whose keys are option names |
| probability | `noul` | none |
| score | `score` | `criteria`: an ordered array of rubric levels, where the first level is score 0 |

For choice mode, a bare CLI choice label is sent with a JSON `null` description. For example, `--choices Heaven,Hell` becomes `"criteria": {"Heaven": null, "Hell": null}`. Choice order must be preserved in the serialized request even though semantic selection does not depend on it.

For System One providers, the client must use the answer at
`answers.question`. ChatGPT's provider codec instead validates the sole entry
in its `answers` array and requires its `name` to be `question`. A successful
HTTP response that does not contain the requested typed answer is a
response-schema error rather than a successful invocation.

The API references used to plan implementation are the
[official TypeSafe OpenAPI document](https://api.typesafe.ai/openapi.json) and
[Vercel's TypeSafe-compatible API documentation](https://vercel.com/docs/ai-gateway/sdks-and-apis/typesafe).
The implementation groups endpoint, default model, credential variables, and
display name into provider profiles so API changes are easy to accommodate.
ChatGPT uses a provider-specific codec because its semantically compatible API
gratuitously renames and reshapes most of the System One wire protocol. The
complete mapping is documented in
[chatgpt-protocol-diff.md](chatgpt-protocol-diff.md).

## 3. Command-line interface

```text
usage: jevq (-c CHOICE[,CHOICE...] | -p | -n | -s) -q QUESTION [--provider {typesafe,vercel,jeff,chatgpt}] [-m MODEL] [-j] [mode options] [TEXT ...]
```

### 3.1 Modes

Exactly one mode is required:

| Short option | Long option | Meaning |
| --- | --- | --- |
| `-c CHOICES` | `--choices CHOICES` | Choice question using a comma-separated option list |
| `-p` | `--probability` | Noul question |
| `-n` | `--noul` | Exact synonym for `--probability` |
| `-s` | `--score` | Score question |

`-p` and `-n` select the same mode; using both is invalid because the mode group is mutually exclusive.

### 3.2 Common arguments

| Argument | Required | Meaning |
| --- | --- | --- |
| `-q QUESTION`, `--question QUESTION` | yes | Instructions sent with the typed question |
| `--provider {typesafe,vercel,jeff,chatgpt}` | no | API provider; defaults to `typesafe` |
| `-m MODEL`, `--model MODEL` | no | Model name or alias; defaults according to the provider |
| `-j`, `--json` | no | Emit machine-readable JSON for both success and error output |
| `TEXT ...` | conditionally | Positional words joined with one ASCII space to form the state |

`QUESTION`, `MODEL`, and the resulting state must each contain at least one non-whitespace character. The model value is passed through unchanged; the API remains authoritative about which model names the authenticated account may use.

### 3.3 Choice arguments

Choice mode is selected with `-c CHOICE[,CHOICE...]` or its long form, `--choices CHOICE[,CHOICE...]`. The choice list is the argument to the mode option itself; no additional flag is required.

- Split the value on literal commas.
- Trim surrounding whitespace from each item.
- Reject empty items and duplicate items after trimming.
- Require at least two choices.
- Reject carriage returns, line feeds, and other ASCII control characters in choice names so plain-text output remains one result per line.
- With the `jeff` provider, reject number-like choice keys because JavaScript
  reorders them and can silently change option order.
- Jeff's current v1.2 models accept at most 254 choices per question.
- Commas inside choice names and per-choice descriptions are not supported in this version.

Example:

```shell
jevq -c Heaven,Hell -q "Where should this one go?" Frank Sinatra
```

### 3.4 Score arguments

The TypeSafe API requires score criteria, so score mode requires `--criteria LEVEL[,LEVEL...]`.

- Split and validate it using the same rules as `--choices`.
- Preserve its order; the first item is score 0, the second is score 1, and so on.
- Require at least two levels at the CLI even though the current API schema permits one; a one-level scale cannot express a meaningful rating.
- With the `jeff` provider, allow at most 10 levels.
- `--criteria` is invalid outside score mode.

Example:

```shell
jevq -s --criteria calm,annoyed,hostile -q "What is the user's hostility level?" < message.txt
```

## 4. State input

The state comes from exactly one of these sources:

1. If one or more positional `TEXT` values are present, join them with a single space. Standard input must not be read.
2. Otherwise, if standard input is not an interactive terminal, read it in full.
3. Otherwise, fail with a usage error explaining that state text or piped stdin is required.

Preserve stdin content exactly, including internal and trailing newlines. Reject an input that is empty or whitespace-only. Do not impose a client-side size limit unless TypeSafe publishes a stable limit; surface the API's validation error if the request is too large.

This precedence makes accidental blocking impossible and makes a positional argument deterministic when stdin is inherited or redirected. Supplying both positional text and redirected stdin is accepted, but the redirected data is ignored.

Examples:

```shell
jevq -p -q "Does this ask for a refund?" < message.txt
printf '%s' 'Please refund my order' | jevq -n -q "Does this ask for a refund?"
```

## 5. Configuration

The selected provider must have any required non-blank credential. TypeSafe
reads `TYPESAFE_API_KEY`. Vercel reads `AI_GATEWAY_API_KEY` first and falls back
to `VERCEL_OIDC_TOKEN`. Jeff optionally reads `JEFF_API_KEY`; no Authorization
header is sent when it is absent. ChatGPT reads `OPENAI_API_KEY`. A missing or
blank required credential is a configuration error detected before any network
request. Credentials must never appear in output, error details, tracebacks,
or logs.

The first version has no command-line API-key option. This avoids leaking credentials through shell history and process listings.

The endpoint is selected by the provider profile and is not otherwise
user-configurable. The model is selected with `--model` or defaults according
to the provider.

## 6. Output contract

Write exactly one result followed by a newline to standard output on success. Do not write progress, labels, Markdown, or diagnostic text to standard output.

### 6.1 Default plain-text mode

Without `-j`/`--json`, print the barest useful result:

| Mode | Output | Example |
| --- | --- | --- |
| choice | selected choice string | `Heaven` |
| noul / probability | decimal probability from 0 to 1 | `0.93` |
| score | decimal score | `1.7` |

Numbers must use Python's normal JSON-compatible decimal representation and must not be rounded or converted to a percentage. A choice is printed verbatim, even if it contains whitespace.

### 6.2 JSON mode

With `-j`/`--json`, write one compact JSON object:

**Choice**

```json
{"choice":"Heaven","probabilities":{"Heaven":0.9,"Hell":0.1},"confidence":0.8}
```

**Noul / probability**

```json
{"noul":0.93}
```

**Score**

```json
{"score":1.7}
```

Choice output copies `choice`, `probabilities`, and `confidence` from the API answer. Noul output copies only `noul`. Score output copies only `score`. Do not round numeric values or substitute percentages from 0 to 100: the API's `noul` is a probability from 0 to 1.

## 7. Errors and exit status

All errors must:

- produce no standard output;
- write exactly one diagnostic followed by a newline to standard error; and
- exit nonzero.

In default mode, the diagnostic is concise plain text prefixed with `jevq: error:`. For example:

```text
jevq: error: exactly one mode is required
```

With `-j`/`--json`, the diagnostic is one compact JSON object:

```json
{"error":{"code":"usage_error","message":"exactly one mode is required"}}
```

The selected format applies to every error, including argument-parser errors. The implementation must therefore detect `-j`/`--json` before full argument validation. In JSON mode, HTTP/API failures may add optional safe details under `error.details`; in plain mode, incorporate only a concise safe message. Neither format may contain the API key or request headers. If the response body is JSON, include only bounded, useful API error fields rather than echoing an arbitrarily large body.

Exit statuses:

| Status | Meaning | Example codes |
| --- | --- | --- |
| 0 | success | — |
| 1 | unexpected internal failure | `internal_error` |
| 2 | invalid CLI usage or input | `usage_error`, `invalid_input` |
| 3 | missing or invalid local configuration | `configuration_error` |
| 4 | network, timeout, TLS, or DNS failure | `network_error`, `timeout` |
| 5 | non-success HTTP response | `authentication_error`, `invalid_request`, `not_ready`, `busy`, `rate_limited`, `api_error` |
| 6 | no usable answer in a successful response | `response_error`, `refusal` |

Suggested HTTP mappings:

- 401 or 403: `authentication_error`
- 400 or 422: `invalid_request`
- 429: `rate_limited`
- 503: `not_ready`
- 529: `busy`
- other non-2xx responses: `api_error`

Use a 30-second request timeout and do not retry in the initial version. This creates predictable command latency and avoids silently repeating a billable request. Reconsider separate connect/read timeouts and bounded retries after initial integration testing provides real failure and latency data.

Unexpected internal exceptions must still use the selected error-output contract. Normal operation must not emit a Python traceback.

## 8. Implementation and quality requirements

- Support Python 3.10 or newer.
- Use `argparse` or an equivalent standard-library parser with a mutually exclusive required mode group.
- Encode and decode JSON with the standard library.
- Send UTF-8 and correctly support Unicode in questions, criteria, and state.
- Do not log input state by default because it may contain sensitive content.
- Make the HTTP transport injectable or otherwise mockable so automated tests do not call the live API.
- Use only the Python standard library in the initial version. Reconsider an HTTP dependency after initial testing if the standard-library transport materially complicates timeout, TLS, or error handling.

## 9. Acceptance criteria

Automated tests should verify at least:

1. each mode produces the expected request body, default plain-text output, and `--json` output;
2. `-p` and `-n` are behaviorally identical;
3. mode exclusivity and required arguments;
4. choice/criteria trimming, empty values, duplicates, control characters, and minimum cardinality;
5. positional state, multiline stdin state, interactive-stdin failure, and positional-over-stdin precedence;
6. each provider's default model, a `--model` override, and a blank model value;
7. provider endpoint selection and missing, blank, and fallback credential handling;
8. Unicode request and response handling;
9. argument-parser, HTTP, and response-schema errors in both output modes;
10. the 30-second timeout and absence of automatic retries;
11. no error path writes to stdout or leaks the API key; and
12. successful JSON-mode output is valid JSON with a trailing newline and no other text.

Live API tests, if added, must be opt-in and skipped when credentials are unavailable.

## 10. Implementation-planning status

No product decision currently blocks implementation planning. Review comments resolved the earlier blockers as follows:

1. **Choice and score syntax:** pass the comma-separated choice list directly to `-c`/`--choices`; use the required comma-separated `--criteria` argument for score mode as described in sections 3.3 and 3.4.
2. **Model selection:** use `-m`/`--model`, defaulting to `jev-latest`.
3. **Output compatibility:** default to bare plain-text results and plain-text errors; use `-j`/`--json` for the reduced JSON results and structured JSON errors.
4. **Initial reliability policy:** use one 30-second request timeout with no automatic retries.

The following decisions are intentionally deferred until after initial testing and do not block implementation:

- whether observed network behavior warrants separate connect/read timeouts, retries, or a third-party HTTP dependency;
- whether to distribute only an executable `jevq.py` or add Python packaging that installs a `jevq` console command; and
- whether to support an endpoint override for test servers or arbitrary providers.

## 11. Corrections to the original draft

- The client has three *question modes* (`-c`, `-p`/`-n`, and `-s`); `-q` supplies question instructions and is not itself a mode.
- The Jev term is **noul**, not “noul/probability percent.” The returned value is a number from 0 to 1.
- The current API calls choice likelihoods `probabilities`, not `scores`.
- Score requests need an ordered rubric; a question alone is insufficient for the current API schema.
- Output is plain text by default; `-j`/`--json` selects machine-readable JSON for successes and errors.
