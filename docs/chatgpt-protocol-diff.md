# ChatGPT Decisions protocol differences

OpenAI's ChatGPT Decisions API is semantically a Jev System One API: it submits
shared evidence, asks typed choice, noul, or score questions, and returns typed
answers with probabilities. It is not wire-compatible. Nearly every container
and identifier around those shared semantics has been renamed or reshaped.

The result is an API that is compatible in concept but gratuitously
incompatible in syntax. The most conspicuous example is renaming Jev's
`noul` type to `predicate`, but that is only one of several needless changes.

The source of truth for the ChatGPT side of this comparison is the
[official OpenAI Decisions API guide](https://developers.openai.com/api/docs/guides/decisions).
The endpoint is currently a public beta and supports only `gpt-6-luna`.

## Summary

| Concept | Jev System One | ChatGPT Decisions | Assessment |
| --- | --- | --- | --- |
| Endpoint | `POST /v1/systemone` | `POST /v1/decisions` | Normal provider difference |
| Credential | `TYPESAFE_API_KEY` | `OPENAI_API_KEY` | Normal provider difference |
| Default model | `jev-latest` | `gpt-6-luna` | Normal provider difference |
| Shared evidence | `state` | `input` | Gratuitous rename |
| Questions | Object keyed by question ID | Array of question objects | Gratuitous reshape |
| Question identity | Object key | `name` property | Gratuitous relocation |
| Boolean question type | `noul` | `predicate` | Gratuitous rename |
| Boolean result | `noul` | `probability` | A second gratuitous rename of the same value |
| Choice options | `criteria` object | `choices` array of objects | Gratuitous reshape |
| Score levels | `criteria` string array | `levels` array of objects | Mostly gratuitous reshape |
| Answers | Object keyed by question ID | Array carrying `name` | Gratuitous mirror-image reshape |
| Choice probabilities | Object keyed by choice | Array of `{value, probability}` | Gratuitous reshape |
| Score probabilities | Object keyed by index plus `legend` | Array of `{value, label, probability}` | Same information rearranged |
| Refusal | No Jevq answer variant | `type: "refusal"` | Legitimate additive behavior |
| Text, message, and image input | Jevq uses text state | Text or user messages with inline images | Legitimate additive behavior |
| Multiple questions | Outside Jevq CLI scope | Explicitly supported | Additive, not needed by Jevq |

In short, semantic compatibility is high and syntactic compatibility is
unnecessarily low.

## Request differences

Jev System One represents the question name structurally:

```json
{
  "model": "jev-latest",
  "state": "Please refund this order.",
  "questions": {
    "question": {
      "type": "noul",
      "instructions": "Does this ask for a refund?"
    }
  }
}
```

ChatGPT renames `state` to `input`, converts `questions` from an object to an
array, moves the existing question key into a `name` field, renames `noul` to
`predicate`, and otherwise expresses the same request:

```json
{
  "model": "gpt-6-luna",
  "input": "Please refund this order.",
  "questions": [
    {
      "type": "predicate",
      "name": "question",
      "instructions": "Does this ask for a refund?"
    }
  ]
}
```

Neither the array nor the `name` property adds a capability that the keyed
object lacked. This is a compatibility break without a corresponding semantic
gain.

### Choice questions

System One encodes choices as keys in `criteria`:

```json
"criteria": {"Heaven": null, "Hell": null}
```

ChatGPT encodes them as objects in a `choices` array:

```json
"choices": [{"value": "Heaven"}, {"value": "Hell"}]
```

The object form can carry descriptions, but descriptions are optional and
Jevq's CLI deliberately accepts labels only. Jevq therefore emits `value`
without inventing descriptions that could alter the decision.

### Score questions

System One uses an ordered array of strings:

```json
"criteria": ["calm", "annoyed", "hostile"]
```

ChatGPT wraps each string in an object and renames the collection:

```json
"levels": [
  {"label": "calm"},
  {"label": "annoyed"},
  {"label": "hostile"}
]
```

This form can also carry descriptions. For Jevq's label-only interface, the
extra object layer provides no benefit.

## Response differences

System One returns an object keyed by the same question ID used in the request.
ChatGPT returns an array and repeats that ID as `name`. Jevq must locate and
validate the named answer rather than merely reading element zero.

For a noul, ChatGPT changes both the type and value field:

```text
System One: {"type":"noul", "noul":0.93}
ChatGPT:    {"type":"predicate", "name":"question", "probability":0.93}
```

Choice distributions also change from a direct lookup object:

```json
{"Heaven": 0.9, "Hell": 0.1}
```

to a list of tiny lookup records:

```json
[
  {"value": "Heaven", "probability": 0.9},
  {"value": "Hell", "probability": 0.1}
]
```

Score distributions make the same change and inline the label that System One
reports through its `legend` object. This is not new decision information; it
is the same information in a less directly addressable representation.

## Jevq compatibility policy

Jevq treats these differences as a provider transport concern. Its CLI and
output contract remain Jev-native:

- `--provider chatgpt` and `--provider openai` are exact synonyms.
- `-p`, `--probability`, and `-n`/`--noul` continue to select noul mode.
- Plain output remains the bare probability, choice, or score.
- JSON noul output remains `{"noul": ...}`.
- Choice probabilities remain an object keyed by the submitted labels.
- Score JSON output remains the reduced `{"score": ...}` object.

The ChatGPT codec performs the gratuitous translations only at the HTTP
boundary:

```text
state -> input
questions object -> questions array plus name
noul -> predicate
noul result -> probability result
criteria map -> choices array
criteria list -> levels array
answers object -> answers array
probability arrays -> Jevq probability maps
```

The codec validates all names, option values, score indices, labels,
probabilities, and confidence values before normalizing the answer. A ChatGPT
`refusal` is reported as a refusal error with exit status 6 because the HTTP
request succeeded but did not produce the requested Jevq answer.

For HTTP 429 responses, Jevq parses only a bounded error body and recognizes a
small allowlist of safe OpenAI error codes. This keeps
`credit_balance_exhausted`, organization/project spend limits, organization
usage limits, and `slow_down` distinct from genuine request-rate exhaustion
without echoing arbitrary provider messages.

Jevq intentionally does not expose ChatGPT's multi-question or image-input
extensions in this change. They are useful additions, but they are outside the
one-question, text-state CLI contract and are unrelated to provider
compatibility.
