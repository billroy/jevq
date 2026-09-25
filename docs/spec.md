# `jevq` command-line client specification

## 1. Purpose

`jevq` is a small Python command-line client for the TypeSafe Jev System One API. It submits one item of input state and one typed question, then writes the relevant typed answer as JSON.

The executable is implemented in `jevq.py`. Packaging or installation must expose it as the `jevq` command; invoking `python3 jevq.py ...` must also work.

This version supports exactly one question per invocation and one of Jev's three question types:

- **choice**: select one member of a caller-supplied finite set;
- **noul**: estimate the probability that a yes/no proposition is true; or
- **score**: rate the state against an ordered, caller-supplied rubric.

Batching multiple questions in one API call, structured (non-string) state, alternate providers, and local caching are out of scope.

## 2. External API contract

Use the TypeSafe API described by its OpenAPI document:

- endpoint: `POST https://api.typesafe.ai/v1/systemone`
- authentication: `Authorization: Bearer <TYPESAFE_API_KEY>`
- content type: `application/json`
- default model: `jev-latest` (subject to the blocking decision in section 10)
- question ID: `question`

The request body has this common shape:

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

The question object changes by mode:

| Mode | API `type` | Additional request field |
| --- | --- | --- |
| choice | `choice` | `criteria`: an object whose keys are option names |
| probability | `noul` | none |
| score | `score` | `criteria`: an ordered array of rubric levels, where the first level is score 0 |

For choice mode, a bare CLI choice label is sent with a JSON `null` description. For example, `--choices Heaven,Hell` becomes `"criteria": {"Heaven": null, "Hell": null}`. Choice order must be preserved in the serialized request even though semantic selection does not depend on it.

The client must use the answer at `answers.question`. A successful HTTP response that is not JSON, has no `answers.question`, or contains an answer type different from the requested type is a response-schema error rather than a successful invocation.

The API reference used to plan implementation is the [official TypeSafe OpenAPI document](https://api.typesafe.ai/openapi.json). The implementation should isolate the endpoint and default model as constants so API changes are easy to accommodate.

## 3. Command-line interface

```text
usage: jevq (-c | -p | -n | -s) -q QUESTION [mode options] [TEXT ...]
```

### 3.1 Modes

Exactly one mode is required:

| Short option | Long option | Meaning |
| --- | --- | --- |
| `-c` | `--choice` | Choice question |
| `-p` | `--probability` | Noul question |
| `-n` | `--noul` | Exact synonym for `--probability` |
| `-s` | `--score` | Score question |

`-p` and `-n` select the same mode; using both is invalid because the mode group is mutually exclusive.

### 3.2 Common arguments

| Argument | Required | Meaning |
| --- | --- | --- |
| `-q QUESTION`, `--question QUESTION` | yes | Instructions sent with the typed question |
| `TEXT ...` | conditionally | Positional words joined with one ASCII space to form the state |

`QUESTION` and the resulting state must each contain at least one non-whitespace character.

### 3.3 Choice arguments

Choice mode requires `--choices CHOICE[,CHOICE...]`.

- Split the value on literal commas.
- Trim surrounding whitespace from each item.
- Reject empty items and duplicate items after trimming.
- Require at least two choices.
- Commas inside choice names and per-choice descriptions are not supported in this version.
- `--choices` is invalid outside choice mode.

Example:

```shell
jevq -c --choices Heaven,Hell -q "Where should this one go?" Frank Sinatra
```

### 3.4 Score arguments

The TypeSafe API requires score criteria, so score mode requires a proposed new argument: `--criteria LEVEL[,LEVEL...]`.

- Split and validate it using the same rules as `--choices`.
- Preserve its order; the first item is score 0, the second is score 1, and so on.
- Require at least two levels at the CLI even though the current API schema permits one; a one-level scale cannot express a meaningful rating.
- `--criteria` is invalid outside score mode.

Example:

```shell
jevq -s --criteria calm,annoyed,hostile -q "What is the user's hostility level?" < message.txt
```

The name and syntax of this argument remain a blocking product decision (section 10); they are specified here as the recommended resolution so implementation can be estimated.

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

`TYPESAFE_API_KEY` is required and must contain a non-whitespace value. A missing or blank key is a configuration error detected before any network request. The key must never appear in output, error details, tracebacks, or logs.

The first version has no command-line API-key option. This avoids leaking credentials through shell history and process listings.

The default endpoint and model are not user-configurable in the initial interface unless section 10 resolves otherwise.

## 6. Output contract

Write exactly one compact JSON value followed by a newline to standard output on success. Do not write progress, labels, Markdown, or diagnostic text to standard output.

Recommended success shapes are:

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

Field names and reduced output versus the complete typed API answer remain a blocking compatibility decision in section 10. The shapes above reconcile the original phrase “relevant portion” with the current API response.

## 7. Errors and exit status

All errors must:

- produce no standard output;
- write exactly one compact JSON object followed by a newline to standard error; and
- exit nonzero.

Error shape:

```json
{"error":{"code":"usage_error","message":"exactly one mode is required"}}
```

For HTTP/API failures, optional safe details may be added under `error.details`; they must not contain the API key or request headers. If the response body is JSON, include only bounded, useful API error fields rather than echoing an arbitrarily large body.

Exit statuses:

| Status | Meaning | Example codes |
| --- | --- | --- |
| 0 | success | — |
| 2 | invalid CLI usage or input | `usage_error`, `invalid_input` |
| 3 | missing or invalid local configuration | `configuration_error` |
| 4 | network, timeout, TLS, or DNS failure | `network_error`, `timeout` |
| 5 | non-success HTTP response | `authentication_error`, `rate_limited`, `api_error` |
| 6 | malformed or unexpected success response | `response_error` |

Suggested HTTP mappings:

- 401 or 403: `authentication_error`
- 429: `rate_limited`
- other non-2xx responses: `api_error`

Use a finite request timeout. The exact connect/read timeout and whether transient failures are retried are blocking reliability decisions in section 10. If retries are adopted, retry only safe transient cases such as connection failures, 429, and 5xx responses; use bounded exponential backoff, honor `Retry-After`, and cap the number of attempts.

Unexpected internal exceptions must still use the JSON error contract. Normal operation must not emit a Python traceback.

## 8. Implementation and quality requirements

- Support Python 3.10 or newer.
- Use `argparse` or an equivalent standard-library parser with a mutually exclusive required mode group.
- Encode and decode JSON with the standard library.
- Send UTF-8 and correctly support Unicode in questions, criteria, and state.
- Do not log input state by default because it may contain sensitive content.
- Make the HTTP transport injectable or otherwise mockable so automated tests do not call the live API.
- Pin and document any third-party runtime dependency. A standard-library-only HTTP implementation is preferred for this small utility unless the project chooses a dependency manager.

## 9. Acceptance criteria

Automated tests should verify at least:

1. each mode produces the expected request body and reduced output;
2. `-p` and `-n` are behaviorally identical;
3. mode exclusivity and required arguments;
4. choice/criteria trimming, empty values, duplicates, and minimum cardinality;
5. positional state, multiline stdin state, interactive-stdin failure, and positional-over-stdin precedence;
6. missing and blank `TYPESAFE_API_KEY` handling;
7. Unicode request and response handling;
8. every documented HTTP and response-schema error class;
9. timeout behavior and any agreed retry policy;
10. no error path writes to stdout or leaks the API key; and
11. successful output is valid JSON with a trailing newline and no other text.

Live API tests, if added, must be opt-in and skipped when credentials are unavailable.

## 10. Decisions blocking implementation planning

The following issues must be resolved before the implementation contract can be considered stable:

1. **Score rubric syntax.** The original score example supplies only a question, but the API requires ordered `criteria`. Approve `--criteria` as specified above, choose another representation (for example repeated `--level` flags), or define a documented default rubric. An implicit generic rubric is not recommended because its semantics would be unclear.
Comment: proceed as proposed here.

2. **Model selection.** The API requires `model`. Confirm a fixed `jev-latest` default, add a `--model` option, or use an environment variable. A fixed alias is simplest but allows upstream behavior to change without a CLI release.
Comment: add a -m --model option defaulting to 'jev-latest'

3. **Success output compatibility.** Confirm whether “choice: return the scores and confidence” means the current API's `choice`, `probabilities`, and `confidence`, and whether noul/score results should be one-field objects as proposed or bare JSON numbers. This affects every downstream caller.
Comment: let's add a -j --json mode.  Default is off.  When off it just prints the barest possible output: the chosen option, the noul bare probability, the bare score.  Errors in plain text.  In -j mode the output is always JSON.

4. **Timeout and retry policy.** Set concrete timeout values and decide whether a command-line tool should retry. Retries improve resilience but can increase latency and repeat billable requests.

The following decisions are important but do not prevent a first implementation if the recommendations in this document are accepted:
Comment: Hold a decision for after initial testing, except where one is needed to proceed.

5. **Dependency strategy.** Choose standard-library HTTP or establish a dependency/packaging mechanism for a library such as `requests`.
6. **Installable command.** Decide whether delivery is only `jevq.py`, an executable script, or an installable Python package exposing the `jevq` console command.

7. **Endpoint override.** A hidden or documented override is useful for testing and future gateways, but it expands the supported configuration surface and can create credential-leak risks if misused.

## 11. Corrections to the original draft

- The client has three *question modes* (`-c`, `-p`/`-n`, and `-s`); `-q` supplies question instructions and is not itself a mode.
- The Jev term is **noul**, not “noul/probability percent.” The returned value is a number from 0 to 1.
- The current API calls choice likelihoods `probabilities`, not `scores`.
- Score requests need an ordered rubric; a question alone is insufficient for the current API schema.
