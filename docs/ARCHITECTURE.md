# Architecture

Flow Review is three layers. The bottom two are a normal installable Python
package; the third is a thin client of it.

```text
┌─────────────────────────────────────────────────────────┐
│ 3. macOS app  (macapp/FlowReviewApp)                     │
│    SwiftUI. Finder entry point, project analysis display,│
│    ambiguity resolution, save panels, Local AI sheet.    │
│    Talks to layer 2 ONLY by running the `flow-review`    │
│    executable as a subprocess. No project inspection,    │
│    config parsing, or build/test logic lives here.       │
├─────────────────────────────────────────────────────────┤
│ 2. CLI  (flow_review.cli)                                │
│    Deterministic argv → stdout/exit-code commands.       │
│    `analyze`, `init`, `run`, `doctor`, `validate`, `list`,│
│    `ai-discover`, `ai-models`, `install-finder`.          │
│    Safe for scripts, CI, and terminal use on its own.     │
├─────────────────────────────────────────────────────────┤
│ 1. Core  (flow_review.discovery/config/inference/codegen/ │
│    simulator/recorder/flows/local_ai/templates)            │
│    Xcode project inspection, config model, inference over │
│    discovered state, flow YAML parsing, Swift/JSON         │
│    codegen, simulator lifecycle, video/result capture.     │
└─────────────────────────────────────────────────────────┘
```

Nothing about the macOS app is required to use Flow Review. Every command it
runs is one you can type yourself; see CLI.md.

## Why this split

The previous Finder integration ran `flow-review desktop <folder>`, which
decided whether to launch a Terminal review or open a *setup assistant* --
either a Tk window or, on Python builds without Tk (common with Homebrew
Python), a chain of eight-plus sequential `osascript` dialogs asking for
values that were almost always derivable from the Xcode project itself. That
UX problem was downstream of a real architectural one: analysis, ambiguity
detection, and UI were not separated, so the only way to add "ask less" logic
was to add it to whichever dialog chain happened to be running.

`inference.py` exists to fix that at the root: it is a pure function of
discovered project state (`ProjectAnalysis` in, `Choice` values out) with no
UI code in it at all. The CLI's `analyze --json` command is just that
function serialized. Both the old CLI flag-based `init` and the new macOS app
call the same inference functions, so "what counts as ambiguous" is defined
in exactly one place. See `src/flow_review/inference.py`.

## The inference contract

`flow-review analyze <project> --json` returns a `ProjectAnalysis`: discovered
facts (targets, schemes, existing config, existing flows, simulators) plus one
`Choice` per setting Flow Review needs (`app_target`, `scheme`, `platform`,
`ui_test_target`, `simulator`). A `Choice` is:

```json
{"value": "Recallable", "options": ["Recallable", "RecallableWatch"],
 "source": "existing_config", "ambiguous": false, "detail": ""}
```

`value` is set whenever Flow Review is confident. `ambiguous` is true only
when `value` is null *and* more than one option exists -- a missing target,
scheme, or simulator is `"unavailable"` (an actionable error), never a
picker with nothing in it. `source` explains *why* a value was chosen
(`existing_config`, `single_candidate`, `name_match`, `booted`, `default`),
which is what the macOS app's "Detected" section shows next to each field.
The macOS app's `Models.swift` mirrors this struct field-for-field via
`JSONDecoder.keyDecodingStrategy = .convertFromSnakeCase`, so the two stay in
sync by construction rather than by hand-kept documentation.

Swift never re-derives a Choice. If a field needs different inference logic,
that change belongs in `inference.py`, covered by `tests/test_inference.py`,
and both the CLI and the app pick it up automatically.

## Destination abstraction

Flow Review reviews against a *destination*, not implicitly "an iPhone
simulator." `config.DeviceConfig.platform` and the Swift
`ReviewDestination` enum (`iOS`, `iPadOS`, `macOS`) are the same three
strings on both sides of the process boundary. `ReviewDestination.
usesSimulator` is the one place the UI branches on "does this need a
simulator row/picker" -- it is `false` only for `.macOS`.

Today the run pipeline (`simulator.py`, `cmd_run`'s `-destination` string)
only implements iOS/iPadOS (via `xcrun simctl` + an iOS Simulator
`xcodebuild` destination) and macOS (a direct `platform=macOS` destination,
no simulator, no video). Adding a new destination -- watchOS is the obvious
next one, given `RecallableWatch`-shaped projects already exist -- means:

1. Add the case to `ReviewDestination` and to `config.py`'s accepted
   `platform` values.
2. Teach `simulator.py` how to pick and boot a device for it (watchOS
   simulators exist in `xcrun simctl` today; `_platform_runtime_key`/
   `_ios_family_devices`-equivalents are the places to extend).
3. Teach `cmd_run`'s destination-string construction and `Recorder` (video
   capture assumes an iOS Simulator UDID today) about the new destination.
4. `inference.infer_platform`'s target→platform mapping
   (`_TARGET_PLATFORM_TO_REVIEW_PLATFORM`) needs the new Xcode SDK family.

No UI code changes beyond adding the enum case -- the picker, the "uses
simulator" branch, and the config round-trip all follow from the enum
already being the single source of truth, which is the point of it existing.

## Save locations: project config vs. user documents

Two different kinds of file live under a Flow Review project, and they are
deliberately not treated the same way:

- **Project-owned infrastructure** -- `flow-review/config.yaml`, the Xcode
  UI-test target, the shared scheme edit. These must live at a known,
  project-relative path because other tooling (the CLI, CI, the next
  developer) needs to find them without being told where they are. Flow
  Review writes these itself, at fixed paths, and that is correct: they are
  not "documents," they are configuration.
- **User-authored output** -- a hand-written or Local-AI-drafted flow YAML,
  a saved draft. These are documents a person is choosing to create, and the
  old assistant silently wrote them to one hard-coded directory
  (`flow-review/suggestions/`) with no way to save anywhere else. The macOS
  app now always goes through `NSSavePanel` for these
  (`AppModel.saveUserDocument` in `macapp/FlowReviewApp/Sources/FlowReviewApp/AppModel.swift`):
  it suggests a project-relative default location the first time, remembers
  the folder a person actually picked (`SaveLocationMemory`, a single
  `UserDefaults` key) for next time, and never assumes.

If you are adding a new kind of output, ask which category it is before
picking where it goes.

## Where each old dialog's question went

| Old dialog | What decided it now |
| --- | --- |
| Choose the app target | `inference.infer_app_target` -- resolved automatically unless truly ambiguous |
| Choose the build scheme | `inference.infer_scheme` -- name-matches the app target, or the single scheme |
| Project name | Advanced field, defaulted from the folder/existing config; rarely needs changing |
| Choose the review platform | `inference.infer_platform` -- derived from the target's Xcode platform |
| Simulator name | `inference.infer_simulator` -- existing config, single install, or the booted device |
| Simulator runtime version | Advanced-only override; inference does not need it in the common case |
| UI-test target name | `inference.infer_ui_test_target` -- never blocks; always resolves to something safe to create |
| "Create configuration?" confirmation | The app's single "Prepare Configuration" action; status updates in place, no OK-click |
| "Configuration created" info dialog | Passive status-list update in the same window |
