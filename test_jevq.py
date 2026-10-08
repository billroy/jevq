from __future__ import annotations

import io
import json
import socket
import unittest
import urllib.error

import jevq


DEFAULT_RESPONSE = object()


class FakeInput(io.StringIO):
    def __init__(self, value: str = "", *, terminal: bool = False, fail_read: bool = False):
        super().__init__(value)
        self.terminal = terminal
        self.fail_read = fail_read

    def isatty(self) -> bool:
        return self.terminal

    def read(self, *args, **kwargs):
        if self.fail_read:
            raise AssertionError("stdin must not be read")
        return super().read(*args, **kwargs)


class FakeResponse:
    def __init__(self, document=None, *, body: bytes | None = None, status: int = 200):
        if body is None:
            body = json.dumps(document).encode("utf-8")
        self.body = body
        self.status = status
        self.closed = False

    def read(self, _size: int = -1) -> bytes:
        return self.body

    def close(self) -> None:
        self.closed = True


class RecordingOpener:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        if self.error is not None:
            raise self.error
        return self.response


def choice_response():
    return {
        "answers": {
            "question": {
                "type": "choice",
                "choice": "Heaven",
                "confidence": 0.8,
                "probabilities": {"Heaven": 0.9, "Hell": 0.1},
            }
        }
    }


def noul_response(value=0.93):
    return {"answers": {"question": {"type": "noul", "noul": value}}}


def score_response(value=1.7):
    return {
        "answers": {
            "question": {
                "type": "score",
                "score": value,
                "confidence": 0.8,
                "legend": {"0": "calm", "1": "annoyed", "2": "hostile"},
                "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8},
            }
        }
    }


def chatgpt_predicate_response(value=0.93):
    return {
        "answers": [
            {
                "type": "predicate",
                "name": "question",
                "probability": value,
            }
        ]
    }


def chatgpt_choice_response():
    return {
        "answers": [
            {
                "type": "choice",
                "name": "question",
                "choice": "Heaven",
                "confidence": 0.8,
                "probabilities": [
                    {"value": "Hell", "probability": 0.1},
                    {"value": "Heaven", "probability": 0.9},
                ],
            }
        ]
    }


def chatgpt_score_response(value=1.7):
    return {
        "answers": [
            {
                "type": "score",
                "name": "question",
                "score": value,
                "confidence": 0.8,
                "probabilities": [
                    {"value": 2, "label": "hostile", "probability": 0.8},
                    {"value": 0, "label": "calm", "probability": 0.1},
                    {"value": 1, "label": "annoyed", "probability": 0.1},
                ],
            }
        ]
    }


class JevqTests(unittest.TestCase):
    def run_main(
        self,
        args,
        *,
        response=DEFAULT_RESPONSE,
        opener=None,
        stdin="",
        terminal=False,
        fail_read=False,
        environ=None,
    ):
        output = io.StringIO()
        error = io.StringIO()
        if opener is None:
            document = noul_response() if response is DEFAULT_RESPONSE else response
            opener = RecordingOpener(FakeResponse(document))
        status = jevq.main(
            args,
            {"TYPESAFE_API_KEY": "test-key"} if environ is None else environ,
            FakeInput(stdin, terminal=terminal, fail_read=fail_read),
            output,
            error,
            opener,
        )
        return status, output.getvalue(), error.getvalue(), opener

    def test_choice_plain_output_and_request(self):
        status, output, error, opener = self.run_main(
            [
                "-c",
                "Heaven,Hell",
                "-q",
                "Where does this one belong?",
                "Frank",
                "Sinatra",
            ],
            response=choice_response(),
            fail_read=True,
        )
        self.assertEqual((status, output, error), (0, "Heaven\n", ""))
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, jevq.API_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(timeout, 30)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["state"], "Frank Sinatra")
        self.assertEqual(
            payload["questions"]["question"],
            {
                "type": "choice",
                "instructions": "Where does this one belong?",
                "criteria": {"Heaven": None, "Hell": None},
            },
        )

    def test_noul_aliases_and_json_output(self):
        for alias in ("-p", "--probability", "-n", "--noul"):
            with self.subTest(alias=alias):
                status, output, error, opener = self.run_main(
                    [alias, "-j", "-q", "Refund?"],
                    response=noul_response(),
                    stdin="Please refund this.\n",
                )
                self.assertEqual((status, output, error), (0, '{"noul":0.93}\n', ""))
                payload = json.loads(opener.calls[0][0].data)
                self.assertEqual(payload["state"], "Please refund this.\n")
                self.assertEqual(payload["questions"]["question"]["type"], "noul")

    def test_score_plain_and_json_outputs(self):
        base = ["-s", "--criteria", "calm,annoyed,hostile", "-q", "Hostility?", "message"]
        status, output, error, _ = self.run_main(base, response=score_response())
        self.assertEqual((status, output, error), (0, "1.7\n", ""))
        status, output, error, _ = self.run_main(
            base + ["-j"], response=score_response()
        )
        self.assertEqual((status, output, error), (0, '{"score":1.7}\n', ""))

    def test_choice_json_output_contains_probabilities_and_confidence(self):
        status, output, error, _ = self.run_main(
            ["--choices", "Heaven,Hell", "-j", "-q", "Destination?", "person"],
            response=choice_response(),
        )
        self.assertEqual(status, 0)
        self.assertEqual(error, "")
        self.assertEqual(
            json.loads(output),
            {
                "choice": "Heaven",
                "probabilities": {"Heaven": 0.9, "Hell": 0.1},
                "confidence": 0.8,
            },
        )

    def test_default_and_overridden_model(self):
        for extra, expected in (([], "jev-latest"), (["-m", "jev-fixed"], "jev-fixed")):
            with self.subTest(expected=expected):
                _, _, _, opener = self.run_main(
                    ["-p", "-q", "Question?", *extra, "state"], response=noul_response()
                )
                payload = json.loads(opener.calls[0][0].data)
                self.assertEqual(payload["model"], expected)

    def test_vercel_provider_uses_gateway_profile(self):
        status, output, error, opener = self.run_main(
            ["--provider", "vercel", "-p", "-q", "Question?", "state"],
            response=noul_response(),
            environ={"AI_GATEWAY_API_KEY": "gateway-key"},
        )
        self.assertEqual((status, output, error), (0, "0.93\n", ""))
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, jevq.VERCEL_PROVIDER.api_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer gateway-key")
        self.assertEqual(timeout, jevq.REQUEST_TIMEOUT_SECONDS)
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "typesafe-ai/jev")

    def test_vercel_provider_accepts_oidc_and_model_override(self):
        _, _, _, opener = self.run_main(
            [
                "--provider",
                "vercel",
                "-p",
                "-m",
                "typesafe-ai/jev-pinned",
                "-q",
                "Question?",
                "state",
            ],
            response=noul_response(),
            environ={
                "AI_GATEWAY_API_KEY": " ",
                "VERCEL_OIDC_TOKEN": "oidc-token",
            },
        )
        request = opener.calls[0][0]
        self.assertEqual(request.get_header("Authorization"), "Bearer oidc-token")
        self.assertEqual(
            json.loads(request.data)["model"], "typesafe-ai/jev-pinned"
        )

    def test_vercel_api_key_takes_precedence_over_oidc(self):
        _, _, _, opener = self.run_main(
            ["--provider", "vercel", "-p", "-q", "Question?", "state"],
            response=noul_response(),
            environ={
                "AI_GATEWAY_API_KEY": "gateway-key",
                "VERCEL_OIDC_TOKEN": "oidc-token",
            },
        )
        self.assertEqual(
            opener.calls[0][0].get_header("Authorization"), "Bearer gateway-key"
        )

    def test_jeff_provider_uses_local_server_without_credentials(self):
        status, output, error, opener = self.run_main(
            ["--provider", "jeff", "-p", "-q", "Question?", "state"],
            response=noul_response(),
            environ={},
        )
        self.assertEqual((status, output, error), (0, "0.93\n", ""))
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, jevq.JEFF_PROVIDER.api_url)
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(timeout, jevq.REQUEST_TIMEOUT_SECONDS)
        self.assertEqual(json.loads(request.data)["model"], "jeff-latest")

    def test_jeff_provider_sends_optional_api_key(self):
        _, _, _, opener = self.run_main(
            ["--provider", "jeff", "-p", "-q", "Question?", "state"],
            response=noul_response(),
            environ={"JEFF_API_KEY": "jeff-key"},
        )
        self.assertEqual(
            opener.calls[0][0].get_header("Authorization"), "Bearer jeff-key"
        )

    def test_jeff_provider_accepts_custom_port(self):
        status, output, error, opener = self.run_main(
            ["--provider", "jeff", "--port", "9876", "-p", "-q", "Question?", "state"],
            response=noul_response(),
            environ={},
        )
        self.assertEqual((status, output, error), (0, "0.93\n", ""))
        self.assertEqual(
            opener.calls[0][0].full_url, "http://localhost:9876/v1/systemone"
        )

    def test_port_is_valid_only_for_jeff_provider(self):
        status, output, error, opener = self.run_main(
            ["--port", "9876", "-p", "-q", "Question?", "state"],
            response=noul_response(),
        )
        self.assertEqual((status, output), (2, ""))
        self.assertIn("only with --provider jeff", error)
        self.assertEqual(opener.calls, [])

    def test_port_must_be_valid_tcp_port(self):
        for value in ("0", "65536", "abc"):
            with self.subTest(value=value):
                status, output, error, opener = self.run_main(
                    ["--provider", "jeff", "--port", value, "-p", "-q", "Q", "state"],
                    environ={},
                )
                self.assertEqual((status, output), (2, ""))
                self.assertIn("port must", error)
                self.assertEqual(opener.calls, [])

    def test_chatgpt_predicate_translates_noul_request_and_response(self):
        status, output, error, opener = self.run_main(
            ["--provider", "chatgpt", "-p", "-j", "-q", "Refund?", "message"],
            response=chatgpt_predicate_response(),
            environ={"OPENAI_API_KEY": "openai-key"},
        )
        self.assertEqual((status, output, error), (0, '{"noul":0.93}\n', ""))
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/decisions")
        self.assertEqual(request.get_header("Authorization"), "Bearer openai-key")
        self.assertEqual(timeout, jevq.REQUEST_TIMEOUT_SECONDS)
        self.assertEqual(
            json.loads(request.data),
            {
                "model": "gpt-6-luna",
                "input": "message",
                "questions": [
                    {
                        "type": "predicate",
                        "name": "question",
                        "instructions": "Refund?",
                    }
                ],
            },
        )

    def test_openai_is_a_synonym_for_chatgpt(self):
        requests = []
        for provider_name in ("chatgpt", "openai"):
            with self.subTest(provider=provider_name):
                status, output, error, opener = self.run_main(
                    [
                        "--provider",
                        provider_name,
                        "-p",
                        "-j",
                        "-q",
                        "Refund?",
                        "message",
                    ],
                    response=chatgpt_predicate_response(),
                    environ={"OPENAI_API_KEY": "openai-key"},
                )
                self.assertEqual((status, output, error), (0, '{"noul":0.93}\n', ""))
                request = opener.calls[0][0]
                requests.append((request.full_url, request.data, request.headers))
        self.assertEqual(requests[0], requests[1])

    def test_chatgpt_choice_translates_request_and_probabilities(self):
        status, output, error, opener = self.run_main(
            [
                "--provider",
                "chatgpt",
                "-c",
                "Heaven,Hell",
                "-j",
                "-q",
                "Destination?",
                "person",
            ],
            response=chatgpt_choice_response(),
            environ={"OPENAI_API_KEY": "openai-key"},
        )
        self.assertEqual(status, 0)
        self.assertEqual(error, "")
        self.assertEqual(
            json.loads(output),
            {
                "choice": "Heaven",
                "probabilities": {"Heaven": 0.9, "Hell": 0.1},
                "confidence": 0.8,
            },
        )
        payload = json.loads(opener.calls[0][0].data)
        self.assertEqual(
            payload["questions"][0],
            {
                "type": "choice",
                "name": "question",
                "instructions": "Destination?",
                "choices": [{"value": "Heaven"}, {"value": "Hell"}],
            },
        )

    def test_chatgpt_score_translates_request_and_validates_distribution(self):
        status, output, error, opener = self.run_main(
            [
                "--provider",
                "chatgpt",
                "-s",
                "--criteria",
                "calm,annoyed,hostile",
                "-q",
                "Hostility?",
                "message",
            ],
            response=chatgpt_score_response(),
            environ={"OPENAI_API_KEY": "openai-key"},
        )
        self.assertEqual((status, output, error), (0, "1.7\n", ""))
        payload = json.loads(opener.calls[0][0].data)
        self.assertEqual(
            payload["questions"][0],
            {
                "type": "score",
                "name": "question",
                "instructions": "Hostility?",
                "levels": [
                    {"label": "calm"},
                    {"label": "annoyed"},
                    {"label": "hostile"},
                ],
            },
        )

    def test_chatgpt_model_override_and_missing_credentials(self):
        _, _, _, opener = self.run_main(
            [
                "--provider",
                "chatgpt",
                "-p",
                "-m",
                "gpt-6-luna-pinned",
                "-q",
                "Q",
                "state",
            ],
            response=chatgpt_predicate_response(),
            environ={"OPENAI_API_KEY": "openai-key"},
        )
        self.assertEqual(
            json.loads(opener.calls[0][0].data)["model"], "gpt-6-luna-pinned"
        )

        for environ in ({}, {"OPENAI_API_KEY": " \t"}):
            with self.subTest(environ=environ):
                status, output, error, opener = self.run_main(
                    ["--provider", "chatgpt", "-p", "-q", "Q", "state"],
                    environ=environ,
                )
                self.assertEqual((status, output), (3, ""))
                self.assertIn("OPENAI_API_KEY", error)
                self.assertEqual(opener.calls, [])

    def test_chatgpt_refusal_is_reported_as_a_refusal(self):
        status, output, error, _ = self.run_main(
            ["--provider", "chatgpt", "-p", "-j", "-q", "Q", "state"],
            response={"answers": [{"type": "refusal", "name": "question"}]},
            environ={"OPENAI_API_KEY": "openai-key"},
        )
        self.assertEqual((status, output), (6, ""))
        self.assertEqual(json.loads(error)["error"]["code"], "refusal")

    def test_chatgpt_rejects_malformed_answer_envelopes_and_distributions(self):
        cases = [
            ({"answers": {}}, ["-p"]),
            ({"answers": []}, ["-p"]),
            (chatgpt_predicate_response(), ["-c", "Heaven,Hell"]),
            (
                {
                    "answers": [
                        {
                            "type": "predicate",
                            "name": "other",
                            "probability": 0.5,
                        }
                    ]
                },
                ["-p"],
            ),
            (
                {
                    "answers": [
                        {
                            "type": "choice",
                            "name": "question",
                            "choice": "Heaven",
                            "confidence": 0.8,
                            "probabilities": [
                                {"value": "Heaven", "probability": 0.9},
                                {"value": "Heaven", "probability": 0.1},
                            ],
                        }
                    ]
                },
                ["-c", "Heaven,Hell"],
            ),
            (
                {
                    "answers": [
                        {
                            "type": "score",
                            "name": "question",
                            "score": 1,
                            "confidence": 0.8,
                            "probabilities": [
                                {"value": 0, "label": "calm", "probability": 0.5},
                                {"value": 1, "label": "hostile", "probability": 0.5},
                            ],
                        }
                    ]
                },
                ["-s", "--criteria", "calm,annoyed"],
            ),
        ]
        for response, mode_args in cases:
            with self.subTest(response=response, mode_args=mode_args):
                status, output, error, _ = self.run_main(
                    [
                        "--provider",
                        "chatgpt",
                        *mode_args,
                        "-j",
                        "-q",
                        "Q",
                        "state",
                    ],
                    response=response,
                    environ={"OPENAI_API_KEY": "openai-key"},
                )
                self.assertEqual((status, output), (6, ""))
                self.assertEqual(
                    json.loads(error)["error"]["code"], "response_error"
                )

    def test_unicode_is_sent_as_utf8(self):
        _, _, _, opener = self.run_main(
            ["-p", "-q", "¿Está bien?", "café", "☕"], response=noul_response()
        )
        body = opener.calls[0][0].data
        self.assertIn("café ☕", body.decode("utf-8"))
        self.assertNotIn(b"\\u2615", body)

    def test_mode_exclusivity_errors_in_plain_and_json(self):
        status, output, error, _ = self.run_main(["-p", "-n", "-q", "Q", "state"])
        self.assertEqual(status, 2)
        self.assertEqual(output, "")
        self.assertTrue(error.startswith("jevq: error:"))

        status, output, error, _ = self.run_main(
            ["-p", "-n", "-j", "-q", "Q", "state"]
        )
        self.assertEqual((status, output), (2, ""))
        self.assertEqual(json.loads(error)["error"]["code"], "usage_error")

    def test_mode_specific_option_rules(self):
        cases = [
            (["-c", "-q", "Q", "state"], "expected one argument"),
            (["-s", "-q", "Q", "state"], "requires --criteria"),
            (["-p", "--criteria", "a,b", "-q", "Q", "state"], "only in score"),
            (["-c", "a,b", "--criteria", "x,y", "-q", "Q", "state"], "only in score"),
        ]
        for args, fragment in cases:
            with self.subTest(args=args):
                status, output, error, _ = self.run_main(args)
                self.assertEqual((status, output), (2, ""))
                self.assertIn(fragment, error)

    def test_label_validation(self):
        values = ["", "one", "one,", ",two", "one, one", "one,tw\no"]
        for value in values:
            with self.subTest(value=value):
                status, output, error, _ = self.run_main(
                    ["-c", value, "-q", "Q", "state"]
                )
                self.assertEqual((status, output != "", error != ""), (2, False, True))

    def test_number_like_choice_keys_are_rejected(self):
        for value in ("1,two", "+1,two", "1.0,two", ".5,two", "1e2,two"):
            with self.subTest(value=value):
                status, output, error, opener = self.run_main(
                    ["--provider", "jeff", "-c", value, "-q", "Q", "state"],
                    environ={},
                )
                self.assertEqual((status, output), (2, ""))
                self.assertIn("bare number", error)
                self.assertEqual(opener.calls, [])

    def test_jeff_option_limits_are_validated_before_request(self):
        cases = [
            (["--provider", "jeff", "-c", ",".join(f"o{i}" for i in range(255))], "254 options"),
            (["--provider", "jeff", "-s", "--criteria", ",".join(f"level{i}" for i in range(11))], "2 to 10 levels"),
        ]
        for mode_args, fragment in cases:
            with self.subTest(fragment=fragment):
                status, output, error, opener = self.run_main(
                    [*mode_args, "-q", "Q", "state"], environ={}
                )
                self.assertEqual((status, output), (2, ""))
                self.assertIn(fragment, error)
                self.assertEqual(opener.calls, [])

    def test_blank_question_model_and_state(self):
        cases = [
            (["-p", "-q", " ", "state"], None),
            (["-p", "-q", "Q", "-m", " ", "state"], None),
            (["-p", "-q", "Q"], " \n"),
        ]
        for args, stdin in cases:
            with self.subTest(args=args):
                status, output, error, _ = self.run_main(args, stdin=stdin or "")
                self.assertEqual((status, output), (2, ""))
                self.assertTrue(error)

    def test_interactive_stdin_requires_state(self):
        status, output, error, _ = self.run_main(
            ["-p", "-q", "Q"], terminal=True
        )
        self.assertEqual((status, output), (2, ""))
        self.assertIn("state text", error)

    def test_state_starting_with_dash_after_separator(self):
        _, _, _, opener = self.run_main(
            ["-p", "-q", "Q", "--", "-state"], response=noul_response()
        )
        payload = json.loads(opener.calls[0][0].data)
        self.assertEqual(payload["state"], "-state")

    def test_json_flag_after_separator_is_positional_text(self):
        status, output, error, _ = self.run_main(
            ["-p", "-q", "Q", "--", "-j"], environ={}
        )
        self.assertEqual((status, output), (3, ""))
        self.assertTrue(error.startswith("jevq: error:"))

    def test_missing_or_blank_api_key(self):
        for environ in ({}, {"TYPESAFE_API_KEY": " \t"}):
            with self.subTest(environ=environ):
                status, output, error, opener = self.run_main(
                    ["-p", "-q", "Q", "state"], environ=environ
                )
                self.assertEqual((status, output), (3, ""))
                self.assertIn("TYPESAFE_API_KEY", error)
                self.assertEqual(opener.calls, [])

    def test_vercel_missing_credentials_names_both_options(self):
        status, output, error, opener = self.run_main(
            ["--provider", "vercel", "-p", "-q", "Q", "state"], environ={}
        )
        self.assertEqual((status, output), (3, ""))
        self.assertIn("AI_GATEWAY_API_KEY", error)
        self.assertIn("VERCEL_OIDC_TOKEN", error)
        self.assertEqual(opener.calls, [])

    def test_help_succeeds_without_key_or_network(self):
        status, output, error, opener = self.run_main(
            ["--help"], environ={}, terminal=True
        )
        self.assertEqual(status, 0)
        self.assertIn("usage: jevq", output)
        self.assertEqual(error, "")
        self.assertEqual(opener.calls, [])

    def test_http_error_mappings(self):
        cases = (
            (400, "invalid_request"),
            (401, "authentication_error"),
            (403, "authentication_error"),
            (422, "invalid_request"),
            (429, "rate_limited"),
            (503, "not_ready"),
            (529, "busy"),
            (500, "api_error"),
        )
        for http_status, code in cases:
            with self.subTest(http_status=http_status):
                failure = urllib.error.HTTPError(
                    jevq.API_URL,
                    http_status,
                    "failed",
                    {},
                    io.BytesIO(b'{"message":"failure"}'),
                )
                opener = RecordingOpener(error=failure)
                status, output, error, _ = self.run_main(
                    ["-p", "-j", "-q", "Q", "state"], opener=opener
                )
                parsed = json.loads(error)
                self.assertEqual((status, output), (5, ""))
                self.assertEqual(parsed["error"]["code"], code)
                self.assertEqual(parsed["error"]["details"]["status"], http_status)

    def test_chatgpt_credit_balance_error_is_not_reported_as_rate_limit(self):
        body = json.dumps(
            {
                "error": {
                    "message": "This untrusted provider message is not echoed.",
                    "type": "insufficient_quota",
                    "code": "credit_balance_exhausted",
                }
            }
        ).encode("utf-8")
        failure = urllib.error.HTTPError(
            jevq.CHATGPT_PROVIDER.api_url,
            429,
            "failed",
            {},
            io.BytesIO(body),
        )
        status, output, error, _ = self.run_main(
            ["--provider", "openai", "-p", "-j", "-q", "Q", "state"],
            opener=RecordingOpener(error=failure),
            environ={"OPENAI_API_KEY": "openai-key"},
        )
        parsed = json.loads(error)
        self.assertEqual((status, output), (5, ""))
        self.assertEqual(parsed["error"]["code"], "credit_balance_exhausted")
        self.assertEqual(
            parsed["error"]["message"],
            "ChatGPT Decisions API credit balance exhausted",
        )
        self.assertEqual(
            parsed["error"]["details"],
            {"status": 429, "provider_code": "credit_balance_exhausted"},
        )
        self.assertNotIn("untrusted provider message", error)

    def test_chatgpt_known_429_codes_and_unknown_fallback(self):
        cases = (
            ("organization_spend_limit_exceeded", "organization spend limit exceeded"),
            ("project_spend_limit_exceeded", "project spend limit exceeded"),
            ("organization_usage_limit_exceeded", "organization usage limit exceeded"),
            ("slow_down", "requested a slower request rate"),
            ("unknown_future_code", "rate limit exceeded"),
            (["invalid", "code"], "rate limit exceeded"),
        )
        for provider_code, message_fragment in cases:
            with self.subTest(provider_code=provider_code):
                response = FakeResponse(
                    {
                        "error": {
                            "type": "rate_limit_error",
                            "code": provider_code,
                        }
                    },
                    status=429,
                )
                status, output, error, _ = self.run_main(
                    ["--provider", "chatgpt", "-p", "-j", "-q", "Q", "state"],
                    opener=RecordingOpener(response),
                    environ={"OPENAI_API_KEY": "openai-key"},
                )
                parsed = json.loads(error)
                self.assertEqual((status, output), (5, ""))
                self.assertIn(message_fragment, parsed["error"]["message"])
                expected_code = (
                    "rate_limited"
                    if provider_code in ("unknown_future_code", ["invalid", "code"])
                    else provider_code
                )
                self.assertEqual(parsed["error"]["code"], expected_code)
                self.assertTrue(response.closed)

    def test_vercel_errors_name_the_selected_provider(self):
        failure = urllib.error.HTTPError(
            jevq.VERCEL_PROVIDER.api_url,
            401,
            "failed",
            {},
            io.BytesIO(b'{}'),
        )
        status, output, error, _ = self.run_main(
            ["--provider", "vercel", "-p", "-q", "Q", "state"],
            opener=RecordingOpener(error=failure),
            environ={"AI_GATEWAY_API_KEY": "gateway-key"},
        )
        self.assertEqual((status, output), (5, ""))
        self.assertIn("Vercel AI Gateway", error)

    def test_timeout_and_network_errors_are_not_retried(self):
        failures = [
            (socket.timeout(), "timeout"),
            (TimeoutError(), "timeout"),
            (urllib.error.URLError(socket.timeout()), "timeout"),
            (urllib.error.URLError("offline"), "network_error"),
            (OSError("connection reset"), "network_error"),
        ]
        for failure, code in failures:
            with self.subTest(failure=type(failure).__name__):
                opener = RecordingOpener(error=failure)
                status, output, error, _ = self.run_main(
                    ["-p", "-j", "-q", "Q", "state"], opener=opener
                )
                self.assertEqual((status, output), (4, ""))
                self.assertEqual(json.loads(error)["error"]["code"], code)
                self.assertEqual(len(opener.calls), 1)

    def test_invalid_json_and_utf8_responses(self):
        for body in (b"not json", b"\xff"):
            with self.subTest(body=body):
                opener = RecordingOpener(FakeResponse(body=body))
                status, output, error, _ = self.run_main(
                    ["-p", "-j", "-q", "Q", "state"], opener=opener
                )
                self.assertEqual((status, output), (6, ""))
                self.assertEqual(json.loads(error)["error"]["code"], "response_error")

    def test_response_envelope_and_type_validation(self):
        documents = [
            [],
            {},
            {"answers": {}},
            {"answers": {"question": []}},
            {"answers": {"question": {"type": "score", "score": 1}}},
        ]
        for document in documents:
            with self.subTest(document=document):
                status, output, error, _ = self.run_main(
                    ["-p", "-j", "-q", "Q", "state"], response=document
                )
                self.assertEqual((status, output), (6, ""))
                self.assertEqual(json.loads(error)["error"]["code"], "response_error")

    def test_noul_rejects_invalid_numbers(self):
        for value in (
            True,
            -0.1,
            1.1,
            float("nan"),
            float("inf"),
            10**1000,
            "0.5",
        ):
            with self.subTest(value=value):
                status, _, error, _ = self.run_main(
                    ["-p", "-j", "-q", "Q", "state"],
                    response=noul_response(value),
                )
                self.assertEqual(status, 6)
                self.assertEqual(json.loads(error)["error"]["code"], "response_error")

    def test_choice_response_validation(self):
        mutations = [
            {"choice": "Other"},
            {"confidence": True},
            {"confidence": 2},
            {"probabilities": {"Heaven": 1}},
            {"probabilities": {"Heaven": 0.5, "Hell": False}},
            {"probabilities": {"Heaven": 0.5, "Hell": 0.5, "Other": float("nan")}},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                document = choice_response()
                document["answers"]["question"].update(mutation)
                status, _, error, _ = self.run_main(
                    ["-c", "Heaven,Hell", "-j", "-q", "Q", "state"],
                    response=document,
                )
                self.assertEqual(status, 6)
                self.assertEqual(json.loads(error)["error"]["code"], "response_error")

    def test_score_response_validation(self):
        mutations = [
            {"score": True},
            {"score": -1},
            {"score": 3},
            {"confidence": None},
            {"legend": []},
            {"legend": {"0": None}},
            {"probabilities": []},
            {"probabilities": {"0": float("nan")}},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                document = score_response()
                document["answers"]["question"].update(mutation)
                status, _, error, _ = self.run_main(
                    ["-s", "--criteria", "calm,annoyed,hostile", "-j", "-q", "Q", "state"],
                    response=document,
                )
                self.assertEqual(status, 6)
                self.assertEqual(json.loads(error)["error"]["code"], "response_error")

    def test_jeff_score_response_does_not_require_legend(self):
        response = score_response()
        del response["answers"]["question"]["legend"]
        status, output, error, _ = self.run_main(
            [
                "--provider",
                "jeff",
                "-s",
                "--criteria",
                "calm,annoyed,hostile",
                "-q",
                "Q",
                "state",
            ],
            response=response,
            environ={},
        )
        self.assertEqual((status, output, error), (0, "1.7\n", ""))

    def test_unexpected_failure_is_sanitized_and_key_is_not_leaked(self):
        secret = "super-secret-key"
        opener = RecordingOpener(error=RuntimeError(secret))
        status, output, error, _ = self.run_main(
            ["-p", "-j", "-q", "Q", "state"],
            opener=opener,
            environ={"TYPESAFE_API_KEY": secret},
        )
        self.assertEqual((status, output), (1, ""))
        self.assertEqual(json.loads(error)["error"]["code"], "internal_error")
        self.assertNotIn(secret, error)
        self.assertNotIn("Traceback", error)

    def test_plain_error_has_single_line_and_no_stdout(self):
        status, output, error, _ = self.run_main([])
        self.assertEqual((status, output), (2, ""))
        self.assertEqual(error.count("\n"), 1)
        self.assertTrue(error.startswith("jevq: error:"))


if __name__ == "__main__":
    unittest.main()
