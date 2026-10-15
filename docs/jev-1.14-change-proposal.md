# Jev 1.14 compatibility change proposal

Date: 2026-10-15

## Release summary

TypeSafe's [Jev 1.14 announcement](https://typesafe.ai/blog/jev-1-14) has two
separate parts:

- `jev-1.14.0` is available through the `jev-preview` alias now. TypeSafe says
  `jev-latest` will move from 1.13 to 1.14 one week after release. The release
  is described as a general quality improvement, with the largest expected
  gain in multilingual use.
- Images, the new Box question type, and 262k context are explicitly
  experimental “danger zone” features. They are opt-in rather than changes to
  ordinary System One requests.

The existing `jevq` request and response contracts for Choice, Noul, and Score
already work with `jev-1.14.0`; callers can select it today with
`--model jev-preview` or `--model jev-1.14.0`. No default-model code change is
needed. Keeping `jev-latest` as the default lets TypeSafe move the stable alias
on its announced schedule.

## Proposed CLI

Add these options:

```text
  -b, --box             find one bounding box (TypeSafe Jev 1.14 only)
  -i, --image FILE      add an image to state; repeat for multiple images
      --long-context    enable TypeSafe's experimental 262k context
```

`--box` joins the existing required, mutually exclusive mode group. `--image`
is repeatable and is valid with Choice, Noul, Score, and Box. `--long-context`
is valid with any TypeSafe mode and does not require an image.

Examples:

```shell
# Image-only Noul; the image is available as `image1` in state.
jevq -n -i receipt.jpg -q "Does `image1` show a total over $100?"

# Text plus two images.
jevq -c same,different -i before.jpg -i after.jpg \
  -q "Are `image1` and `image2` the same product?" "inspection photos"

# Experimental Box output requires Jev 1.14.
jevq -b -i kitchen.jpg -m jev-preview -q "the coffee mug in `image1`"

# Raise both request and per-question context limits to 262k.
jevq -n --long-context -q "Does this contract permit early termination?" \
  < contract.txt
```

Use `-i`, not `--i`, as the short spelling; conventional short options have
one hyphen. Do not add a second opt-in such as `--danger-zone-images`:
specifying `--image` or `--box` is already an explicit request for the feature,
and the client can add the required header automatically.

## Request construction

### Images in state

Text-only calls retain their current string `state` and JSON encoding exactly.
When one or more images are supplied, build structured state instead:

```json
{
  "text": "optional positional or stdin text",
  "image1": "7cd0...",
  "image2": "b181..."
}
```

Omit `text` for an image-only call. Each value is an unguessable, per-request
asset reference. The stable field names `image1`, `image2`, and so on let a
question refer to an image without exposing a local filename to the model.
Image-only input satisfies the state requirement; a call with neither text nor
an image continues to fail before reading credentials or making a request.

The corresponding top-level assets are:

```json
{
  "assets": {
    "7cd0...": {"type": "image", "part": "jevq_image_1"},
    "b181...": {"type": "image", "part": "jevq_image_2"}
  }
}
```

Send image calls as `multipart/form-data`, which TypeSafe recommends over
base64 JSON. The first part is named `request` and contains the JSON document;
each image is a uniquely named file part. Use generic upload filenames such as
`image1.jpg` so local paths and filenames are not disclosed. Continue using
the current JSON transport for text-only calls.

Image requests automatically send:

```text
TypeSafe-Danger-Zone-Images: true
```

`--long-context` sends:

```text
TypeSafe-Danger-Zone-Long-Context: true
```

The two headers compose when both features are used.

### Provider capability checks

Add capability fields to `ProviderProfile` instead of key-name conditionals:

```text
supports_images
supports_box
supports_long_context
```

Initially enable all three only for the direct `typesafe` provider. Reject an
unsupported combination as a usage error before opening image files. The
release and TypeSafe documentation specify the direct API contract, while the
current Vercel, ChatGPT, and local-provider contracts in this repository do
not document these headers, multipart assets, or Box answers. The flags make
it a small follow-up to enable a provider once its contract is verified.

Do not change Vercel's model ID or the local/ChatGPT codecs as part of this
work.

### File validation

Before constructing the multipart body:

- require a readable regular file;
- accept `.png`, `.jpg`, and `.jpeg` case-insensitively;
- check the PNG/JPEG signature rather than trusting the suffix alone;
- reject a file over 16 MiB using metadata before reading it; and
- let TypeSafe remain authoritative for decoding, animation, dimensions,
  total pixels, and expanded-request/context limits.

The documented image limits are PNG or JPEG, 16 MiB per file, dimensions from
256 through 1568 pixels on each side, 78,400 through 2,458,624 total pixels,
and 32 MiB body/image/expanded-request ceilings. Duplicating an asset
reference duplicates its expanded size and token use. The README should state
these limits and note that callers must rotate EXIF-oriented photos and resize
images themselves; the API neither rotates nor resizes them.

This validation keeps `jevq` standard-library-only. It deliberately avoids a
partial image decoder whose behavior could disagree with the service.

## Box mode

Build the question as:

```json
{"type": "box", "instructions": "the target in `image1`"}
```

Box has no criteria, requires at least one `--image`, and requires the direct
TypeSafe provider. When `--model` is omitted in Box mode, use the pinned
`jev-1.14.0` model so the new mode works immediately while `jev-latest` still
points at 1.13. Preserve an explicit model unchanged, but reject the known-
incompatible `jev-1.13.0` with a clear usage error; unknown names and moving
aliases remain server-validated. Documentation should also show
`--model jev-preview` for callers who deliberately want the moving preview.

Validate a successful answer as follows:

- `type` is `box`;
- `box` is an object with exactly numeric `top`, `bottom`, `left`, and `right`;
- all four values are finite and in `[0, 1]`;
- `left < right` and `top < bottom`; and
- `confidence` is finite and in `[0, 1]`.

As with other numeric validation, booleans are not numbers. In default output
mode, emit the compact box object because a box has no honest scalar form:

```json
{"top":0.25,"bottom":0.82,"left":0.44,"right":0.84}
```

In `--json` mode, retain confidence:

```json
{"box":{"top":0.25,"bottom":0.82,"left":0.44,"right":0.84},"confidence":0.92}
```

Document the important semantic caveat: Box always returns one box, even when
the target is absent. TypeSafe recommends pairing it with a Noul presence
question. Because `jevq` intentionally asks only one question, that safer
paired pattern would require a later multi-question feature; it should not be
hidden behind a second network call.

## Long context

`--long-context` should only add the documented header. Do not add client-side
token estimation or automatically turn the feature on based on input size.
TypeSafe says the opt-in raises both the per-request and per-question limits
from 64k/32k to 262k, but is slower and less accurate. Automatic use would
change both latency and answer quality without the caller's consent.

## Code changes

### `jevq.py`

1. Extend `ProviderProfile` with the three capability flags.
2. Extend `CliConfig` with image paths and the long-context choice.
3. Add `box` to mode parsing and add repeatable `-i/--image` plus
   `--long-context`.
4. Split state acquisition into optional text acquisition and image-aware state
   construction so image-only calls do not try to read an interactive stdin.
5. Add image preflight and an immutable image/asset value carrying the path,
   format, reference, and multipart part name.
6. Extend `build_payload` for structured image state, `assets`, and Box.
7. Refactor `call_api` to accept a prepared body and headers, with separate JSON
   and multipart preparation helpers. Keep the opener injection and timeout.
8. Add capability-specific headers without exposing a general arbitrary-header
   CLI option.
9. Extend answer extraction and success rendering for Box.
10. Preserve the current error/exit contract and never include image bytes,
    local paths, request bodies, or multipart boundaries in diagnostics.

### `test_jevq.py`

Add offline tests for:

- `-i` and `--image`, repeated images, image-only state, and text-plus-image
  state;
- stable `image1`/`image2` fields and unique opaque asset/part references;
- multipart ordering, part names, content types, generic filenames, raw file
  bytes, authorization, danger-zone headers, and the existing timeout;
- images plus long context sending both headers;
- Box payloads and plain/JSON output;
- malformed Box coordinates, confidence, missing fields, booleans, non-finite
  values, reversed/zero-area boxes, and wrong answer types;
- missing, unreadable, non-regular, unsupported, bad-signature, and oversized
  image files;
- rejection of image/Box/long-context options for unsupported providers before
  file I/O or an opener call;
- Box without an image and known Jev 1.13 selection;
- unchanged exact JSON payloads and headers for all existing text-only tests;
  and
- help text and JSON-formatted parser errors for the new options.

Use temporary files for fixtures, parse the generated multipart boundary, and
assert that references are opaque and internally consistent. No test should
call the live API.

### Documentation

- Update `README.md` with a “Jev 1.14 and experimental features” section,
  examples, limits, model-alias timing, provider restrictions, and the Box
  absence caveat.
- Update `docs/spec.md` to make image-only state, multipart requests, headers,
  Box request/answer validation, and output normative.
- Update `docs/implementation-plan.md` with the new helper boundaries and test
  matrix.

## Other announcement items

- **Multilingual improvements:** no new wire format is required. Add a release
  note recommending per-language evaluation and explicit model pinning when
  thresholds matter.
- **Alias transition and deprecation:** retain `jev-latest`; document
  `jev-preview` for early testing and `jev-1.14.0` for pinned evaluations. Add
  no date-based client logic.
- **Opt-in product feedback:** the announcement says users can opt in to help
  TypeSafe find model jaggedness and says queries still will not be used for
  training. It does not publish a request header or System One body field for
  that setting. Do not invent a CLI switch. Once TypeSafe documents the
  control, document the console/account setting or add a flag only if it is
  genuinely request-scoped.

## Suggested delivery slices

1. **Compatibility/docs:** confirm existing Choice/Noul/Score fixtures against
   the 1.14 schema; document `-m jev-preview` and the alias rollout.
2. **Image transport:** add repeatable `-i/--image`, structured state,
   capability checks, multipart encoding, headers, and tests.
3. **Box:** add the mode, strict response validation, output contract, and
   experimental warnings.
4. **Long context:** add the explicit flag/header and documentation.
5. **Optional live smoke tests:** behind an environment flag, exercise one
   small image Noul and one Box against `jev-preview`; never run them in the
   default suite.

## Acceptance criteria

- Existing text-only invocations produce the same request bytes and output as
  before.
- `jevq -n -i FILE ...` sends valid TypeSafe multipart with assets and the image
  danger-zone header.
- `jevq -b -i FILE ...` selects `jev-1.14.0`, validates the response, and emits
  a Box answer; an explicit `-m jev-preview` also works.
- `--long-context` sends only the documented long-context header.
- Experimental options fail locally for providers without a verified contract.
- All new behavior is covered without network access, the full unit suite
  passes, and `git diff --check` is clean.
