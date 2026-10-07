# jevq

`jevq` asks one Choice, Noul, or Score question using Jev's TypeSafe-compatible
System One API. It supports TypeSafe directly, Vercel AI Gateway, a local Jeff
server, and ChatGPT's Decisions API through a compatibility adapter.

It requires Python 3.10 or newer and uses only the Python standard library.
TypeSafe is the default provider and reads `TYPESAFE_API_KEY`.

```shell
python3 jevq.py -c Heaven,Hell -q "Where should this one go?" Frank Sinatra
python3 jevq.py -p -q "Does this ask for a refund?" < message.txt
python3 jevq.py -s --criteria calm,annoyed,hostile -q "What is the user's hostility level?" < message.txt
```

Output is the selected choice, probability, or score as plain text. Pass `-j`
or `--json` for JSON success and error output, and `-m` or `--model` to
override the selected provider's default model.

## Vercel AI Gateway

Select Vercel with `--provider vercel`. The provider reads
`AI_GATEWAY_API_KEY`, falling back to `VERCEL_OIDC_TOKEN` on Vercel deployments,
and defaults to the `typesafe-ai/jev` model.

```shell
export AI_GATEWAY_API_KEY="your_ai_gateway_api_key"
python3 jevq.py --provider vercel -p -q "Does this ask for a refund?" < message.txt
```

An explicit `--model` overrides the selected provider's default model.

## ChatGPT Decisions API

Select ChatGPT with `--provider chatgpt` or its exact synonym
`--provider openai`. The provider reads `OPENAI_API_KEY`, calls
`https://api.openai.com/v1/decisions`, and defaults to `gpt-6-luna`.

```shell
export OPENAI_API_KEY="your_openai_api_key"
python3 jevq.py --provider chatgpt -p \
  -q "Does this ask for a refund?" < message.txt
```

Jevq translates ChatGPT's incompatible `predicate` terminology and array-based
wire format back to the existing Jev CLI and output contract. See the
[protocol comparison](docs/chatgpt-protocol-diff.md) for the full catalog of
otherwise gratuitous differences.

## Jeff

Start `jeff-serve` as described in the [Jeff getting-started guide](https://jeffhub.ai/docs/getting-started),
then select it with `--provider jeff`. The provider calls
`http://localhost:8765/v1/systemone`, defaults to `jeff-latest`, and does not
require credentials unless the server has authentication enabled. Set
`JEFF_API_KEY` when it does.

```shell
python3 jevq.py --provider jeff -p -q "Does this ask for a refund?" < message.txt
python3 jevq.py --provider jeff -c refunds,deliveries -m support-intents \
  -q "Which team should handle this?" < message.txt
```

Jeff choice keys cannot be bare numbers; use names such as `o1` or short words.

## Simple install

For a single-user install, run the installer from the repository:

```shell
./install.sh
```

It creates the destination directory if needed and installs the executable at
`~/.local/bin/jevq`. Ensure `~/.local/bin` is on your `PATH`.

Pass a destination to install somewhere else, such as `/usr/local/bin`:

```shell
sudo ./install.sh /usr/local/bin/jevq
```

After installing, run `jevq` directly:

```shell
jevq -c Heaven,Hell -q "Where should this one go?" Frank Sinatra
```

## Return codes

`jevq` exits with `0` when it prints a successful answer. On failure, it writes the error to standard error and exits with one of these nonzero statuses:

| Code | Meaning |
| --- | --- |
| 1 | Unexpected internal failure |
| 2 | Invalid command-line usage or input |
| 3 | Missing or invalid provider credentials |
| 4 | Network, timeout, TLS, or DNS failure |
| 5 | Non-success provider HTTP response |
| 6 | Missing, refused, malformed, or unexpected successful provider answer |

With `--json`, errors use the same return codes and are emitted as JSON with an
`error.code` such as `usage_error`, `configuration_error`, `timeout`,
`authentication_error`, `rate_limited`, `api_error`, `response_error`, or
`refusal`. Recognized OpenAI 429 responses retain safe, specific codes such as
`credit_balance_exhausted`, `organization_spend_limit_exceeded`,
`project_spend_limit_exceeded`, and `organization_usage_limit_exceeded`.

See [the specification](docs/spec.md) for the complete interface and [the implementation plan](docs/implementation-plan.md) for design details.

Run tests with:

```shell
python3 -m unittest -v
```
