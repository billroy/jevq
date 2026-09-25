# jevq

`jevq` asks one Choice, Noul, or Score question using the TypeSafe Jev System One API.

It requires Python 3.10 or newer and a `TYPESAFE_API_KEY` environment variable. The initial implementation uses only the Python standard library.

```shell
python3 jevq.py -c Heaven,Hell -q "Where should this one go?" Frank Sinatra
python3 jevq.py -p -q "Does this ask for a refund?" < message.txt
python3 jevq.py -s --criteria calm,annoyed,hostile -q "What is the user's hostility level?" < message.txt
```

Output is the selected choice, probability, or score as plain text. Pass `-j` or `--json` for JSON success and error output, and `-m` or `--model` to override the default `jev-latest` model.

## Return codes

`jevq` exits with `0` when it prints a successful answer. On failure, it writes the error to standard error and exits with one of these nonzero statuses:

| Code | Meaning |
| --- | --- |
| 1 | Unexpected internal failure |
| 2 | Invalid command-line usage or input |
| 3 | Missing or invalid local configuration, such as `TYPESAFE_API_KEY` |
| 4 | Network, timeout, TLS, or DNS failure |
| 5 | Non-success TypeSafe API HTTP response |
| 6 | Malformed or unexpected successful TypeSafe API response |

With `--json`, errors use the same return codes and are emitted as JSON with an `error.code` such as `usage_error`, `configuration_error`, `timeout`, `authentication_error`, `rate_limited`, `api_error`, or `response_error`.

See [the specification](docs/spec.md) for the complete interface and [the implementation plan](docs/implementation-plan.md) for design details.

Run tests with:

```shell
python3 -m unittest -v
```
