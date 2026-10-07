#!/usr/bin/env python3
"""Command-line client for Jev's TypeSafe-compatible System One API."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence, TextIO


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
class ProviderProfile:
    key: str
    display_name: str
    api_url: str
    default_model: str
    credential_envs: tuple[str, ...]
    credential_required: bool = True
    wire_protocol: str = "systemone"


TYPESAFE_PROVIDER = ProviderProfile(
    key="typesafe",
    display_name="TypeSafe",
    api_url="https://api.typesafe.ai/v1/systemone",
    default_model="jev-latest",
    credential_envs=("TYPESAFE_API_KEY",),
)
VERCEL_PROVIDER = ProviderProfile(
    key="vercel",
    display_name="Vercel AI Gateway",
    api_url="https://ai-gateway.vercel.sh/typesafe/v1/systemone",
    default_model="typesafe-ai/jev",
    credential_envs=("AI_GATEWAY_API_KEY", "VERCEL_OIDC_TOKEN"),
)
JEFF_PROVIDER = ProviderProfile(
    key="jeff",
    display_name="Jeff",
    api_url="http://localhost:8765/v1/systemone",
    default_model="jeff-latest",
    credential_envs=("JEFF_API_KEY",),
    credential_required=False,
)
CHATGPT_PROVIDER = ProviderProfile(
    key="chatgpt",
    display_name="ChatGPT Decisions API",
    api_url="https://api.openai.com/v1/decisions",
    default_model="gpt-6-luna",
    credential_envs=("OPENAI_API_KEY",),
    wire_protocol="openai_decisions",
)
PROVIDERS = {
    provider.key: provider
    for provider in (
        TYPESAFE_PROVIDER,
        VERCEL_PROVIDER,
        JEFF_PROVIDER,
        CHATGPT_PROVIDER,
    )
}
PROVIDERS["openai"] = CHATGPT_PROVIDER

# Backwards-compatible names for callers that imported the original constants.
API_URL = TYPESAFE_PROVIDER.api_url
DEFAULT_MODEL = TYPESAFE_PROVIDER.default_model


@dataclass(frozen=True)
class CliConfig:
    provider: ProviderProfile
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
        description="Ask one typed question using a Jev System One provider.",
        output=output,
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "-c",
        "--choices",
        dest="choices",
        metavar="CHOICE[,CHOICE...]",
        help="ask a choice question using comma-separated choices",
    )
    modes.add_argument(
        "-p", "--probability", dest="mode", action="store_const", const="noul"
    )
    modes.add_argument("-n", "--noul", dest="mode", action="store_const", const="noul")
    modes.add_argument("-s", "--score", dest="mode", action="store_const", const="score")

    parser.add_argument("-q", "--question", required=True, help="question instructions")
    parser.add_argument(
        "--provider",
        choices=tuple(PROVIDERS),
        default=TYPESAFE_PROVIDER.key,
        help="API provider (default: typesafe)",
    )
    parser.add_argument(
        "-m", "--model", help="model name (defaults to the provider's model)"
    )
    parser.add_argument("-j", "--json", action="store_true", help="emit JSON output")
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


NUMBER_LIKE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


def parse_cli(argv: Sequence[str], output: TextIO) -> CliConfig:
    args = make_parser(output).parse_args(list(argv))

    provider = PROVIDERS[args.provider]
    mode = "choice" if args.choices is not None else args.mode
    model = provider.default_model if args.model is None else args.model

    if not args.question.strip():
        raise JevqError("invalid_input", "question must not be blank", 2)
    if not model.strip():
        raise JevqError("invalid_input", "model must not be blank", 2)

    labels: tuple[str, ...] = ()
    if mode == "choice":
        if args.criteria is not None:
            raise JevqError("usage_error", "--criteria is valid only in score mode", 2)
        labels = parse_labels(args.choices, "--choices")
        number_like = next(
            (
                label
                for label in labels
                if provider is JEFF_PROVIDER and NUMBER_LIKE.match(label)
            ),
            None,
        )
        if number_like is not None:
            raise JevqError(
                "invalid_input",
                f"choice {number_like!r} is a bare number; use a key such as 'o1' or a short word",
                2,
            )
        if provider is JEFF_PROVIDER and len(labels) > 254:
            raise JevqError(
                "invalid_input", "Jeff choice questions support at most 254 options", 2
            )
    elif mode == "score":
        if args.criteria is None:
            raise JevqError("usage_error", "score mode requires --criteria", 2)
        labels = parse_labels(args.criteria, "--criteria")
        if provider is JEFF_PROVIDER and len(labels) > 10:
            raise JevqError(
                "invalid_input", "Jeff score questions support 2 to 10 levels", 2
            )
    else:
        if args.criteria is not None:
            raise JevqError("usage_error", "--criteria is valid only in score mode", 2)

    return CliConfig(
        provider=provider,
        mode=mode,
        question=args.question,
        model=model,
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


def load_api_key(
    environ: Mapping[str, str], provider: ProviderProfile = TYPESAFE_PROVIDER
) -> str | None:
    for variable in provider.credential_envs:
        api_key = environ.get(variable)
        if api_key is not None and api_key.strip():
            return api_key

    if not provider.credential_required:
        return None

    variable_names = " or ".join(provider.credential_envs)
    raise JevqError(
        "configuration_error", f"{variable_names} is missing or blank", 3
    )


def build_payload(config: CliConfig, state: str) -> dict[str, Any]:
    if config.provider.wire_protocol == "openai_decisions":
        question: dict[str, Any] = {
            "type": "predicate" if config.mode == "noul" else config.mode,
            "name": QUESTION_ID,
            "instructions": config.question,
        }
        if config.mode == "choice":
            question["choices"] = [{"value": label} for label in config.labels]
        elif config.mode == "score":
            question["levels"] = [{"label": label} for label in config.labels]

        return {
            "model": config.model,
            "input": state,
            "questions": [question],
        }

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


OPENAI_429_ERRORS = {
    "credit_balance_exhausted": "credit balance exhausted",
    "organization_spend_limit_exceeded": "organization spend limit exceeded",
    "project_spend_limit_exceeded": "project spend limit exceeded",
    "organization_usage_limit_exceeded": "organization usage limit exceeded",
    "slow_down": "requested a slower request rate",
    "insufficient_quota": "API quota exhausted",
}


def _openai_429_error(
    body: bytes, provider: ProviderProfile
) -> JevqError | None:
    if provider.wire_protocol != "openai_decisions" or not body:
        return None
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict) or not isinstance(document.get("error"), dict):
        return None

    error = document["error"]
    provider_code = error.get("code")
    if not isinstance(provider_code, str) or provider_code not in OPENAI_429_ERRORS:
        provider_code = error.get("type")
    if not isinstance(provider_code, str) or provider_code not in OPENAI_429_ERRORS:
        return None

    details = {"status": 429, "provider_code": provider_code}
    return JevqError(
        provider_code,
        f"{provider.display_name} {OPENAI_429_ERRORS[provider_code]}",
        5,
        details,
    )


def _http_error(
    status: int, provider: ProviderProfile, body: bytes = b""
) -> JevqError:
    details = {"status": status}
    if status in (401, 403):
        return JevqError(
            "authentication_error",
            f"{provider.display_name} authentication failed",
            5,
            details,
        )
    if status == 429:
        openai_error = _openai_429_error(body, provider)
        if openai_error is not None:
            return openai_error
        return JevqError(
            "rate_limited",
            f"{provider.display_name} rate limit exceeded",
            5,
            details,
        )
    if status in (400, 422):
        return JevqError(
            "invalid_request", f"{provider.display_name} rejected the request", 5, details
        )
    if status == 503:
        return JevqError(
            "not_ready", f"{provider.display_name} is not ready", 5, details
        )
    if status == 529:
        return JevqError("busy", f"{provider.display_name} is busy", 5, details)
    return JevqError(
        "api_error", f"{provider.display_name} returned HTTP {status}", 5, details
    )


def _read_response(response: Any) -> bytes:
    try:
        return response.read()
    finally:
        close = getattr(response, "close", None)
        if close is not None:
            close()


def _read_error_body(response: Any) -> bytes:
    try:
        try:
            body = response.read(ERROR_BODY_LIMIT + 1)
        except OSError:
            return b""
        if len(body) > ERROR_BODY_LIMIT:
            return b""
        return body
    finally:
        close = getattr(response, "close", None)
        if close is not None:
            close()


def call_api(
    payload: Mapping[str, Any],
    api_key: str | None,
    opener: Callable[..., Any] | None = None,
    *,
    provider: ProviderProfile = TYPESAFE_PROVIDER,
) -> Any:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        provider.api_url,
        data=body,
        headers=headers,
        method="POST",
    )
    open_request = opener or urllib.request.urlopen

    try:
        response = open_request(request, timeout=REQUEST_TIMEOUT_SECONDS)
        status = getattr(response, "status", None)
        if status is None and hasattr(response, "getcode"):
            status = response.getcode()
        if status is not None and not 200 <= int(status) < 300:
            error_body = _read_error_body(response)
            raise _http_error(int(status), provider, error_body)
        response_body = _read_response(response)
    except urllib.error.HTTPError as error:
        error_body = _read_error_body(error)
        raise _http_error(error.code, provider, error_body) from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, (socket.timeout, TimeoutError)):
            raise JevqError(
                "timeout", f"{provider.display_name} request timed out", 4
            ) from None
        raise JevqError(
            "network_error", f"{provider.display_name} request failed", 4
        ) from None
    except (socket.timeout, TimeoutError):
        raise JevqError(
            "timeout", f"{provider.display_name} request timed out", 4
        ) from None
    except OSError:
        raise JevqError(
            "network_error", f"{provider.display_name} request failed", 4
        ) from None

    try:
        decoded = response_body.decode("utf-8")
        return json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise JevqError(
            "response_error",
            f"{provider.display_name} returned an invalid JSON response",
            6,
        ) from None


def _is_finite_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _probability(
    value: Any, field: str, provider: ProviderProfile = TYPESAFE_PROVIDER
) -> float | int:
    if not _is_finite_number(value) or not 0 <= value <= 1:
        raise JevqError(
            "response_error", f"{provider.display_name} response has invalid {field}", 6
        )
    return value


def _openai_probabilities(
    value: Any,
    labels: Sequence[str],
    provider: ProviderProfile,
) -> dict[str, float | int]:
    if not isinstance(value, list) or len(value) != len(labels):
        raise JevqError(
            "response_error",
            f"{provider.display_name} response has invalid choice probabilities",
            6,
        )

    probabilities: dict[str, float | int] = {}
    for item in value:
        if not isinstance(item, dict):
            raise JevqError(
                "response_error",
                f"{provider.display_name} response has invalid choice probabilities",
                6,
            )
        label = item.get("value")
        if not isinstance(label, str) or label not in labels or label in probabilities:
            raise JevqError(
                "response_error",
                f"{provider.display_name} response has invalid choice probabilities",
                6,
            )
        probabilities[label] = _probability(
            item.get("probability"), f"probability for {label!r}", provider
        )

    if set(probabilities) != set(labels):
        raise JevqError(
            "response_error",
            f"{provider.display_name} response has invalid choice probabilities",
            6,
        )
    return {label: probabilities[label] for label in labels}


def _validate_openai_score_probabilities(
    value: Any,
    labels: Sequence[str],
    provider: ProviderProfile,
) -> None:
    if not isinstance(value, list) or len(value) != len(labels):
        raise JevqError(
            "response_error",
            f"{provider.display_name} response has invalid score probabilities",
            6,
        )

    seen: set[int] = set()
    for item in value:
        if not isinstance(item, dict):
            raise JevqError(
                "response_error",
                f"{provider.display_name} response has invalid score probabilities",
                6,
            )
        index = item.get("value")
        label = item.get("label")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < len(labels)
            or index in seen
            or label != labels[index]
        ):
            raise JevqError(
                "response_error",
                f"{provider.display_name} response has invalid score probabilities",
                6,
            )
        _probability(
            item.get("probability"), f"score probability {index!r}", provider
        )
        seen.add(index)

    if seen != set(range(len(labels))):
        raise JevqError(
            "response_error",
            f"{provider.display_name} response has invalid score probabilities",
            6,
        )


def _extract_openai_answer(
    document: Any,
    mode: str,
    labels: Sequence[str],
    provider: ProviderProfile,
) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise JevqError(
            "response_error", f"{provider.display_name} response must be an object", 6
        )
    answers = document.get("answers")
    if not isinstance(answers, list) or len(answers) != 1:
        raise JevqError(
            "response_error",
            f"{provider.display_name} response must contain one answer",
            6,
        )
    answer = answers[0]
    if not isinstance(answer, dict) or answer.get("name") != QUESTION_ID:
        raise JevqError(
            "response_error",
            f"{provider.display_name} response is missing answer named {QUESTION_ID!r}",
            6,
        )
    if answer.get("type") == "refusal":
        raise JevqError(
            "refusal", f"{provider.display_name} refused the question", 6
        )

    expected_type = "predicate" if mode == "noul" else mode
    if answer.get("type") != expected_type:
        raise JevqError(
            "response_error",
            f"{provider.display_name} response has the wrong answer type",
            6,
        )
    if mode == "noul":
        return {
            "noul": _probability(answer.get("probability"), "probability", provider)
        }
    if mode == "choice":
        choice = answer.get("choice")
        if not isinstance(choice, str) or choice not in labels:
            raise JevqError(
                "response_error",
                f"{provider.display_name} response has an invalid choice",
                6,
            )
        confidence = _probability(answer.get("confidence"), "confidence", provider)
        probabilities = _openai_probabilities(
            answer.get("probabilities"), labels, provider
        )
        return {
            "choice": choice,
            "probabilities": probabilities,
            "confidence": confidence,
        }

    score = answer.get("score")
    if not _is_finite_number(score) or score < 0 or score > len(labels) - 1:
        raise JevqError(
            "response_error", f"{provider.display_name} response has an invalid score", 6
        )
    _probability(answer.get("confidence"), "confidence", provider)
    _validate_openai_score_probabilities(answer.get("probabilities"), labels, provider)
    return {"score": score}


def extract_answer(
    document: Any,
    mode: str,
    labels: Sequence[str],
    provider: ProviderProfile = TYPESAFE_PROVIDER,
) -> dict[str, Any]:
    if provider.wire_protocol == "openai_decisions":
        return _extract_openai_answer(document, mode, labels, provider)

    if not isinstance(document, dict):
        raise JevqError(
            "response_error", f"{provider.display_name} response must be an object", 6
        )
    answers = document.get("answers")
    if not isinstance(answers, dict) or QUESTION_ID not in answers:
        raise JevqError(
            "response_error",
            f"{provider.display_name} response is missing answers.question",
            6,
        )
    answer = answers[QUESTION_ID]
    if not isinstance(answer, dict) or answer.get("type") != mode:
        raise JevqError(
            "response_error",
            f"{provider.display_name} response has the wrong answer type",
            6,
        )

    if mode == "noul":
        return {"noul": _probability(answer.get("noul"), "noul", provider)}

    if mode == "choice":
        choice = answer.get("choice")
        if not isinstance(choice, str) or choice not in labels:
            raise JevqError(
                "response_error",
                f"{provider.display_name} response has an invalid choice",
                6,
            )
        confidence = _probability(answer.get("confidence"), "confidence", provider)
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, dict) or any(
            label not in probabilities for label in labels
        ):
            raise JevqError(
                "response_error",
                f"{provider.display_name} response has invalid choice probabilities",
                6,
            )
        if provider is JEFF_PROVIDER and set(probabilities) != set(labels):
            raise JevqError(
                "response_error",
                "Jeff response has invalid choice probabilities",
                6,
            )
        for label, probability in probabilities.items():
            if not isinstance(label, str):
                raise JevqError(
                    "response_error",
                    f"{provider.display_name} response has invalid choice probabilities",
                    6,
                )
            _probability(probability, f"probability for {label!r}", provider)
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
        raise JevqError(
            "response_error", f"{provider.display_name} response has an invalid score", 6
        )
    _probability(answer.get("confidence"), "confidence", provider)
    legend = answer.get("legend")
    if (provider is not JEFF_PROVIDER and legend is None) or (
        legend is not None
        and (
            not isinstance(legend, dict)
            or any(
                not isinstance(level, str)
                or not isinstance(description, (str, dict, list))
                for level, description in legend.items()
            )
        )
    ):
        raise JevqError(
            "response_error",
            f"{provider.display_name} response has an invalid score legend",
            6,
        )
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict):
        raise JevqError(
            "response_error",
            f"{provider.display_name} response has invalid score probabilities",
            6,
        )
    if provider is JEFF_PROVIDER and set(probabilities) != {
        str(index) for index in range(len(labels))
    }:
        raise JevqError(
            "response_error", "Jeff response has invalid score probabilities", 6
        )
    for level, probability in probabilities.items():
        if not isinstance(level, str):
            raise JevqError(
                "response_error",
                f"{provider.display_name} response has invalid score probabilities",
                6,
            )
        _probability(probability, f"score probability {level!r}", provider)
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
        api_key = load_api_key(environment, config.provider)
        payload = build_payload(config, state)
        document = call_api(payload, api_key, opener, provider=config.provider)
        answer = extract_answer(
            document, config.mode, config.labels, provider=config.provider
        )
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
