# Flow Review

Python tooling that turns small YAML UI flows into generated XCUITests and review artifacts.

**Status:** Prototype for Apple-platform testing. Running generated UI tests needs macOS and Xcode. Linear flows intentionally do not replace arbitrary handwritten UI automation.

[Project portfolio](https://zacharysturman.com/projects/flow-review)

Xcode project inspection also requires Ruby and the `xcodeproj` gem (`gem install xcodeproj`).

## Quick start

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

## Usage

```sh
.venv/bin/flow-review --help
```

## How it works

The Python core discovers project configuration, validates linear flows and generates test sources. The Mac interface calls the CLI; Xcode project mutation is isolated in a Ruby helper.

## Testing

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

See [validation and next steps](docs/NEXT_STEPS.md) for the checks performed on this snapshot. Automated fixture tests do not establish physical-device compatibility.

## Configuration and privacy

This is a curated source snapshot, published November 12, 2026. Configure your own endpoints, credentials and signing identities. Private development history, local environment files, personal recordings, runtime databases, signing materials and deployment automation are excluded. Deployment and device installation are explicit operator actions.

## Further reading

- [Architecture](docs/ARCHITECTURE.md)

## License and attribution

Existing licenses and third-party notices apply to the files they cover. Publication of this snapshot does not grant a new license to original material; no additional license is granted where none is supplied.
