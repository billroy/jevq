#!/usr/bin/env python3
"""Command-line client for the TypeSafe Jev System One API."""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence, TextIO


API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
REQUEST_TIMEOUT_SECONDS = 30
QUESTION_ID = "question"
ERROR_BODY_LIMIT = 64 * 1024


class JevqError(Exception):
    """An expected, user-facing failure."""

    def __init__(
        self,
        code: str,
        message: str,
        exit_status: int,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_status = exit_status
        self.details = dict(details) if details else None


class HelpRequested(Exception):
    """Signal that argparse printed help successfully."""


class JevqArgumentParser(argparse.ArgumentParser):
    """Argument parser that delegates all failures to the output contract."""

    def __init__(self, *args: Any, output: TextIO, **kwargs: Any) -> None:
        self._output = output
        super().__init__(*args, **kwargs)

    def print_help(self, file: TextIO | None = None) -> None:
        super().print_help(file or self._output)

    def error(self, message: str) -> None:
        raise JevqError("usage_error", message, 2)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if message:
            self._print_message(message, self._output)
        if status == 0:
            raise HelpRequested
        raise JevqError("usage_error", message or "argument parsing failed", 2)


@dataclass(frozen=True)
class CliConfig:
    mode: str
    question: str
    model: str
    json_mode: bool
    labels: tuple[str, ...]
    text_parts: tuple[str, ...]


def detect_json_mode(argv: Sequence[str]) -> bool:
    """Detect JSON mode before argparse can report an error."""

    for argument in argv:
        if argument == "--":
            break
        if argument in ("-j", "--json"):
            return True
    return False


def make_parser(output: TextIO) -> JevqArgumentParser:
    parser = JevqArgumentParser(
        prog="jevq",
        description="Ask one typed question using the TypeSafe Jev API.",
        output=output,
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("-c", "--choice", dest="mode", action="store_const", const="choice")
    modes.add_argument(
        "-p", "--probability", dest="mode", action="store_const", const="noul"
    )
    modes.add_argument("-n", "--noul", dest="mode", action="store_const", const="noul")
    modes.add_argument("-s", "--score", dest="mode", action="store_const", const="score")

    parser.add_argument("-q", "--question", required=True, help="question instructions")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help="TypeSafe model name")
    parser.add_argument("-j", "--json", action="store_true", help="emit JSON output")
    parser.add_argument("--choices", help="comma-separated choices for choice mode")
    parser.add_argument("--criteria", help="comma-separated ordered levels for score mode")
    parser.add_argument("text", nargs="*", metavar="TEXT", help="state text")
    return parser


def parse_labels(value: str, option_name: str) -> tuple[str, ...]:
    raw_items = value.split(",")
    labels: list[str] = []
    seen: set[str] = set()

    for raw_item in raw_items:
        if any(ord(character) < 32 or ord(character) == 127 for character in raw_item):
            raise JevqError(
                "invalid_input",
                f"{option_name} values must not contain ASCII control characters",
                2,
            )
        label = raw_item.strip()
        if not label:
            raise JevqError(
                "invalid_input", f"{option_name} must not contain empty values", 2
            )
        if label in seen:
            raise JevqError(
                "invalid_input", f"{option_name} contains duplicate value {label!r}", 2
            )
        labels.append(label)
        seen.add(label)

    if len(labels) < 2:
        raise JevqError(
            "invalid_input", f"{option_name} requires at least two values", 2
        )
    return tuple(labels)


def parse_cli(argv: Sequence[str], output: TextIO) -> CliConfig:
    args = make_parser(output).parse_args(list(argv))

    if not args.question.strip():
        raise JevqError("invalid_input", "question must not be blank", 2)
    if not args.model.strip():
        raise JevqError("invalid_input", "model must not be blank", 2)

    labels: tuple[str, ...] = ()
    if args.mode == "choice":
        if args.choices is None:
            raise JevqError("usage_error", "choice mode requires --choices", 2)
        if args.criteria is not None:
            raise JevqError("usage_error", "--criteria is valid only in score mode", 2)
        labels = parse_labels(args.choices, "--choices")
    elif args.mode == "score":
        if args.criteria is None:
            raise JevqError("usage_error", "score mode requires --criteria", 2)
        if args.choices is not None:
            raise JevqError("usage_error", "--choices is valid only in choice mode", 2)
        labels = parse_labels(args.criteria, "--criteria")
    else:
        if args.choices is not None:
            raise JevqError("usage_error", "--choices is valid only in choice mode", 2)
        if args.criteria is not None:
            raise JevqError("usage_error", "--criteria is valid only in score mode", 2)

    return CliConfig(
        mode=args.mode,
        question=args.question,
        model=args.model,
        json_mode=args.json,
        labels=labels,
        text_parts=tuple(args.text),
    )


def read_state(text_parts: Sequence[str], stdin: TextIO) -> str:
    if text_parts:
        state = " ".join(text_parts)
    else:
        if stdin.isatty():
            raise JevqError(
                "usage_error", "state text or piped standard input is required", 2
            )
        state = stdin.read()

    if not state.strip():
        raise JevqError("invalid_input", "state must not be blank", 2)
    return state


def load_api_key(environ: Mapping[str, str]) -> str:
    api_key = environ.get("TYPESAFE_API_KEY")
    if api_key is None or not api_key.strip():
        raise JevqError(
            "configuration_error", "TYPESAFE_API_KEY is missing or blank", 3
        )
    return api_key


def build_payload(config: CliConfig, state: str) -> dict[str, Any]:
    question: dict[str, Any] = {
        "type": config.mode,
        "instructions": config.question,
    }
    if config.mode == "choice":
        question["criteria"] = {label: None for label in config.labels}
    elif config.mode == "score":
        question["criteria"] = list(config.labels)

    return {
        "model": config.model,
        "state": state,
        "questions": {QUESTION_ID: question},
    }


def _http_error(status: int) -> JevqError:
    details = {"status": status}
    if status in (401, 403):
        return JevqError(
            "authentication_error", "TypeSafe authentication failed", 5, details
        )
    if status == 429:
        return JevqError("rate_limited", "TypeSafe rate limit exceeded", 5, details)
    return JevqError(
        "api_error", f"TypeSafe API returned HTTP {status}", 5, details
    )


def _read_response(response: Any) -> bytes:
    try:
        return response.read()
    finally:
        close = getattr(response, "close", None)
        if close is not None:
            close()


def _discard_error_body(response: Any) -> None:
    try:
        try:
            response.read(ERROR_BODY_LIMIT + 1)
        except OSError:
            pass
    finally:
        close = getattr(response, "close", None)
        if close is not None:
            close()


def call_api(
    payload: Mapping[str, Any],
    api_key: str,
    opener: Callable[..., Any] | None = None,
) -> Any:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    open_request = opener or urllib.request.urlopen

    try:
        response = open_request(request, timeout=REQUEST_TIMEOUT_SECONDS)
        status = getattr(response, "status", None)
        if status is None and hasattr(response, "getcode"):
            status = response.getcode()
        if status is not None and not 200 <= int(status) < 300:
            _discard_error_body(response)
            raise _http_error(int(status))
        response_body = _read_response(response)
    except urllib.error.HTTPError as error:
        _discard_error_body(error)
        raise _http_error(error.code) from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, (socket.timeout, TimeoutError)):
            raise JevqError("timeout", "TypeSafe API request timed out", 4) from None
        raise JevqError("network_error", "TypeSafe API request failed", 4) from None
    except (socket.timeout, TimeoutError):
        raise JevqError("timeout", "TypeSafe API request timed out", 4) from None
    except OSError:
        raise JevqError("network_error", "TypeSafe API request failed", 4) from None

    try:
        decoded = response_body.decode("utf-8")
        return json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise JevqError(
            "response_error", "TypeSafe API returned an invalid JSON response", 6
        ) from None


def _is_finite_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _probability(value: Any, field: str) -> float | int:
    if not _is_finite_number(value) or not 0 <= value <= 1:
        raise JevqError(
            "response_error", f"TypeSafe response has invalid {field}", 6
        )
    return value


def extract_answer(
    document: Any, mode: str, labels: Sequence[str]
) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise JevqError("response_error", "TypeSafe response must be an object", 6)
    answers = document.get("answers")
    if not isinstance(answers, dict) or QUESTION_ID not in answers:
        raise JevqError(
            "response_error", "TypeSafe response is missing answers.question", 6
        )
    answer = answers[QUESTION_ID]
    if not isinstance(answer, dict) or answer.get("type") != mode:
        raise JevqError(
            "response_error", "TypeSafe response has the wrong answer type", 6
        )

    if mode == "noul":
        return {"noul": _probability(answer.get("noul"), "noul")}

    if mode == "choice":
        choice = answer.get("choice")
        if not isinstance(choice, str) or choice not in labels:
            raise JevqError(
                "response_error", "TypeSafe response has an invalid choice", 6
            )
        confidence = _probability(answer.get("confidence"), "confidence")
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, dict) or any(
            label not in probabilities for label in labels
        ):
            raise JevqError(
                "response_error", "TypeSafe response has invalid choice probabilities", 6
            )
        for label, probability in probabilities.items():
            if not isinstance(label, str):
                raise JevqError(
                    "response_error",
                    "TypeSafe response has invalid choice probabilities",
                    6,
                )
            _probability(probability, f"probability for {label!r}")
        return {
            "choice": choice,
            "probabilities": probabilities,
            "confidence": confidence,
        }

    score = answer.get("score")
    if (
        not _is_finite_number(score)
        or score < 0
        or score > len(labels) - 1
    ):
        raise JevqError("response_error", "TypeSafe response has an invalid score", 6)
    _probability(answer.get("confidence"), "confidence")
    legend = answer.get("legend")
    if not isinstance(legend, dict) or any(
        not isinstance(level, str) or not isinstance(description, (str, dict, list))
        for level, description in legend.items()
    ):
        raise JevqError(
            "response_error", "TypeSafe response has an invalid score legend", 6
        )
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict):
        raise JevqError(
            "response_error", "TypeSafe response has invalid score probabilities", 6
        )
    for level, probability in probabilities.items():
        if not isinstance(level, str):
            raise JevqError(
                "response_error", "TypeSafe response has invalid score probabilities", 6
            )
        _probability(probability, f"score probability {level!r}")
    return {"score": score}


def write_success(
    answer: Mapping[str, Any], mode: str, json_mode: bool, stdout: TextIO
) -> None:
    if json_mode:
        value = json.dumps(answer, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    elif mode == "choice":
        value = str(answer["choice"])
    elif mode == "noul":
        value = str(answer["noul"])
    else:
        value = str(answer["score"])
    stdout.write(value + "\n")


def write_error(error: JevqError, json_mode: bool, stderr: TextIO) -> None:
    if json_mode:
        payload: dict[str, Any] = {
            "error": {"code": error.code, "message": error.message}
        }
        if error.details:
            payload["error"]["details"] = error.details
        message = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
    else:
        message = f"jevq: error: {error.message}"
    stderr.write(message + "\n")


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    opener: Callable[..., Any] | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    environment = os.environ if environ is None else environ
    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    error_stream = sys.stderr if stderr is None else stderr
    json_mode = detect_json_mode(arguments)

    try:
        config = parse_cli(arguments, output_stream)
        state = read_state(config.text_parts, input_stream)
        api_key = load_api_key(environment)
        payload = build_payload(config, state)
        document = call_api(payload, api_key, opener)
        answer = extract_answer(document, config.mode, config.labels)
        write_success(answer, config.mode, config.json_mode, output_stream)
        return 0
    except HelpRequested:
        return 0
    except JevqError as error:
        write_error(error, json_mode, error_stream)
        return error.exit_status
    except Exception:
        error = JevqError("internal_error", "unexpected internal failure", 1)
        write_error(error, json_mode, error_stream)
        return error.exit_status


if __name__ == "__main__":
    raise SystemExit(main())
