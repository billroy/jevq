# jevq

`jevq` asks one Choice, Noul, or Score question using the TypeSafe Jev System One API.

It requires Python 3.10 or newer and a `TYPESAFE_API_KEY` environment variable. The initial implementation uses only the Python standard library.

```shell
python3 jevq.py -c --choices Heaven,Hell -q "Where should this one go?" Frank Sinatra
python3 jevq.py -p -q "Does this ask for a refund?" < message.txt
python3 jevq.py -s --criteria calm,annoyed,hostile -q "What is the user's hostility level?" < message.txt
```

Output is the selected choice, probability, or score as plain text. Pass `-j` or `--json` for JSON success and error output, and `-m` or `--model` to override the default `jev-latest` model.

See [the specification](docs/spec.md) for the complete interface and [the implementation plan](docs/implementation-plan.md) for design details.

Run tests with:

```shell
python3 -m unittest -v
```
