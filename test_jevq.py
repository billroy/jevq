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

    def test_help_succeeds_without_key_or_network(self):
        status, output, error, opener = self.run_main(
            ["--help"], environ={}, terminal=True
        )
        self.assertEqual(status, 0)
        self.assertIn("usage: jevq", output)
        self.assertEqual(error, "")
        self.assertEqual(opener.calls, [])

    def test_http_error_mappings(self):
        for http_status, code in ((401, "authentication_error"), (403, "authentication_error"), (429, "rate_limited"), (500, "api_error")):
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
