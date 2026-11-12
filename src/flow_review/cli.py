"""Command-line entry point for configuring and running Flow Review.

The CLI deliberately owns orchestration only: flow parsing, Swift/JSON
generation, simulator lifecycle, and Xcode project edits remain in their
small focused modules. Commands print paths to all durable artifacts so the
review loop stays visible and scriptable.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from . import codegen, config, desktop, discovery, finder, inference, local_ai, macapp, paths, procutil, simulator
from .flows import Flow, FlowError, load_flows, select_flows
from .recorder import Recorder


class CLIError(RuntimeError):
    """An actionable command-line error."""


def _project_root(value: str, require_config: bool = True) -> Path:
    start = Path(value).expanduser().resolve()
    if not start.is_dir():
        raise CLIError(f"Project directory does not exist: {start}")
    return config.find_project_root(start) if require_config else start


def _relative_to_root(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _configured_path(root: Path, value: Optional[str], label: str) -> Path:
    if not value:
        raise CLIError(f"flow-review/config.yaml is missing xcode.{label}")
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.exists():
        raise CLIError(f"Configured {label} does not exist: {path}")
    return path


def _xcode_project_path(root: Path, cfg: config.ProjectConfig) -> Path:
    project = _configured_path(root, cfg.xcode.project, "project")
    if project.suffix != ".xcodeproj":
        raise CLIError(f"xcode.project must name a .xcodeproj, got: {project}")
    return project


def _build_container(root: Path, cfg: config.ProjectConfig) -> tuple[str, Path]:
    if cfg.xcode.workspace:
        return "-workspace", _configured_path(root, cfg.xcode.workspace, "workspace")
    return "-project", _xcode_project_path(root, cfg)


def _require_xcode_settings(cfg: config.ProjectConfig) -> tuple[str, str]:
    missing = [
        field for field, value in (("scheme", cfg.xcode.scheme), ("ui_test_target", cfg.xcode.ui_test_target))
        if not value
    ]
    if missing:
        raise CLIError("flow-review/config.yaml is missing xcode." + " and xcode.".join(missing))
    return cfg.xcode.scheme or "", cfg.xcode.ui_test_target or ""


def _resolve(label: str, flag: str, requested: Optional[str], choice: inference.Choice) -> str:
    """Prefer an explicit CLI override, otherwise take what inference decided.

    Raises with the same actionable, available-options message whether the
    override was invalid or inference could not disambiguate on its own.
    """
    if requested:
        if choice.options and requested not in choice.options:
            names = ", ".join(choice.options) or "none"
            raise CLIError(f"{label} '{requested}' was not found. Available: {names}")
        return requested
    if choice.is_resolved:
        return choice.value  # type: ignore[return-value]
    names = ", ".join(choice.options) or "none"
    reason = choice.detail or f"Could not choose {label.lower()}."
    raise CLIError(f"{reason} Pass {flag}. Available: {names}")


def _choose_app_target(info: discovery.ProjectInfo, requested: Optional[str]) -> discovery.TargetInfo:
    name = _resolve("App target", "--app-target", requested, inference.infer_app_target(info, None))
    return next(t for t in info.app_targets if t.name == name)


def _choose_scheme(info: discovery.ProjectInfo, app_target: str, requested: Optional[str]) -> str:
    return _resolve("Scheme", "--scheme", requested, inference.infer_scheme(info, app_target, None))


def _choose_ui_test_target(info: discovery.ProjectInfo, app_target: str, requested: Optional[str]) -> str:
    if requested:
        return requested
    return inference.infer_ui_test_target(info, app_target, None).value  # type: ignore[return-value]


def _sync(root: Path, cfg: config.ProjectConfig, flows: list[Flow], delay_ms: int) -> dict:
    project = _xcode_project_path(root, cfg)
    _, ui_test_target = _require_xcode_settings(cfg)
    return codegen.sync(root, project, ui_test_target, flows, delay_ms)


def _flow_test_identifier(ui_test_target: str, flow: Flow) -> str:
    return f"{ui_test_target}/{codegen.GENERATED_TEST_CLASS}/{flow.swift_test_name()}"


def build_xcodebuild_command(
    root: Path,
    cfg: config.ProjectConfig,
    ui_test_target: str,
    flow: Flow,
    destination: str,
    result_bundle: Path,
) -> list[str]:
    flag, container = _build_container(root, cfg)
    scheme, _ = _require_xcode_settings(cfg)
    return [
        "xcodebuild", "test", flag, str(container), "-scheme", scheme,
        "-configuration", cfg.xcode.build_configuration,
        "-destination", destination,
        "-only-testing:" + _flow_test_identifier(ui_test_target, flow),
        "-resultBundlePath", str(result_bundle),
    ]


def _write_log(path: Path, result: procutil.ProcResult) -> None:
    path.write_text(result.stdout + ("\n" if result.stdout and result.stderr else "") + result.stderr)


def _check_executable(name: str) -> tuple[bool, str]:
    found = shutil.which(name)
    return (bool(found), found or f"'{name}' was not found on PATH")


def cmd_init(args: argparse.Namespace) -> int:
    root = _project_root(args.project_root, require_config=False)
    config_path = paths.config_file(root)
    if config_path.exists() and not args.force:
        raise CLIError(
            f"A Flow Review config already exists at {config_path}. "
            "Use `flow-review init --force` only if you intend to replace it."
        )

    info = discovery.discover(root)
    if not info.xcodeproj:
        raise CLIError(
            "Flow Review needs an .xcodeproj to add its generated UI-test target. "
            "A workspace may be configured as the build container, but the project must be present too."
        )

    app_target = _choose_app_target(info, args.app_target)
    scheme = _choose_scheme(info, app_target.name, args.scheme)
    ui_test_target = _choose_ui_test_target(info, app_target.name, args.ui_test_target)
    helper_result = procutil.xcode_helper("ensure_ui_test_target", {
        "project_path": str(info.xcodeproj.resolve()),
        "app_target_name": app_target.name,
        "ui_test_target_name": ui_test_target,
        "scheme_name": scheme,
    })

    cfg = config.ProjectConfig(
        id=config.new_project_id(),
        name=args.name or root.name,
        device=config.DeviceConfig(platform=args.platform, name=args.device, os_version=args.os_version),
        xcode=config.XcodeConfig(
            project=_relative_to_root(info.xcodeproj, root),
            workspace=_relative_to_root(info.xcworkspace, root) if info.xcworkspace else None,
            scheme=scheme,
            app_target=app_target.name,
            ui_test_target=ui_test_target,
            bundle_id=app_target.bundle_id,
        ),
        run=config.RunConfig(),
        root=root,
    )
    cfg.save()
    cfg.flows_path.mkdir(parents=True, exist_ok=True)
    flows = load_flows(cfg.flows_path)
    sync_result = _sync(root, cfg, flows, cfg.run.review_delay_ms)

    action = "created" if helper_result.get("created") else "reused"
    print(f"Initialized Flow Review in {root}")
    print(f"UI-test target: {ui_test_target} ({action})")
    print(f"Config: {paths.config_file(root)}")
    print(f"Flows: {cfg.flows_path} ({len(flows)} found)")
    print(f"Generated files: {codegen.generated_dir(root, ui_test_target)}")
    if sync_result.get("added"):
        print("Added generated Xcode files: " + ", ".join(sync_result["added"]))
    print("Next: run `flow-review doctor` and add a YAML flow before `flow-review run`.")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    root = _project_root(args.project_root)
    cfg = config.load_config(root)
    project = _xcode_project_path(root, cfg)
    scheme, ui_test_target = _require_xcode_settings(cfg)
    flows = load_flows(cfg.flows_path)
    if not flows:
        raise CLIError(f"No flow YAML files found in {cfg.flows_path}")
    print(f"Valid configuration: {paths.config_file(root)}")
    print(f"Build project: {project}")
    print(f"Scheme: {scheme}; UI-test target: {ui_test_target}")
    print("Valid flows: " + ", ".join(flow.id for flow in flows))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    root = _project_root(args.project_root)
    cfg = config.load_config(root)
    flows = load_flows(cfg.flows_path)
    if not flows:
        print(f"No flows found in {cfg.flows_path}")
        return 0
    for flow in flows:
        tags = ", ".join(flow.tags) or "-"
        platform = flow.platform or "all"
        print(f"{flow.id}\t{platform}\t{tags}\t{flow.name}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    root = _project_root(args.project_root, require_config=False)
    checks: list[tuple[str, bool, str]] = []
    for executable in ("xcodebuild", "xcrun", "ruby"):
        ok, detail = _check_executable(executable)
        checks.append((executable, ok, detail))

    if shutil.which("ruby"):
        result = procutil.run(["ruby", "-e", "require 'xcodeproj'; puts Xcodeproj::VERSION"], timeout=20)
        checks.append(("Ruby xcodeproj gem", result.ok, result.stdout.strip() or result.stderr.strip()))

    try:
        cfg = config.load_config(root)
        project = _xcode_project_path(root, cfg)
        scheme, ui_test_target = _require_xcode_settings(cfg)
        checks.extend([
            ("flow-review config", True, str(paths.config_file(root))),
            ("Xcode project", True, str(project)),
            ("scheme", True, scheme),
            ("UI-test target", True, ui_test_target),
        ])
        flows = load_flows(cfg.flows_path)
        checks.append(("flow YAML", bool(flows), f"{len(flows)} flow(s) in {cfg.flows_path}"))
        if cfg.device.platform.lower() not in ("macos", "osx"):
            devices = simulator.list_devices("ios")
            wanted = simulator.find_or_create_device(cfg.device.platform, cfg.device.name, cfg.device.os_version)
            checks.append(("simulator", bool(devices), f"{wanted.name} ({wanted.state})"))
    except (config.ConfigError, CLIError, FlowError, simulator.SimulatorError) as exc:
        checks.append(("project configuration", False, str(exc).replace("\n", " ")))

    failed = False
    for name, ok, detail in checks:
        marker = "OK" if ok else "FAIL"
        print(f"{marker:4} {name}: {detail}")
        failed = failed or not ok
    if failed:
        print("\nFix the failed checks, then run `flow-review doctor` again.")
        return 1
    print("\nFlow Review is ready to run this project.")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    """Report what Flow Review can infer about a project without changing anything.

    This is the machine-readable entry point the macOS app uses to build its
    single consolidated setup window instead of a chain of dialogs; `--json`
    is exactly the contract that app reads.
    """
    root = Path(args.project_root).expanduser().resolve()
    if not root.is_dir():
        raise CLIError(f"Project directory does not exist: {root}")
    analysis = inference.analyze(root)
    if args.json:
        import json
        print(json.dumps(analysis.to_json(), indent=2))
        return 0
    for step in analysis.steps:
        print(step)
    for err in analysis.errors:
        print(f"! {err}")
    print()
    if analysis.ready_to_run:
        print(f"Ready to run ({analysis.flow_count} flow(s)).")
    elif analysis.needs_setup:
        print(f"Needs setup: {analysis.blocking_reason}")
    return 0


def _selected_flows(cfg: config.ProjectConfig, args: argparse.Namespace) -> list[Flow]:
    flows = load_flows(cfg.flows_path)
    selected = select_flows(
        flows,
        flow_ids=args.flow,
        tags=args.tag,
        platform=cfg.device.platform,
    )
    if not selected:
        selector = "the requested flow/tag filters" if args.flow or args.tag else f"platform {cfg.device.platform}"
        raise CLIError(f"No flows match {selector} in {cfg.flows_path}")
    return selected


def _run_flow(
    root: Path,
    cfg: config.ProjectConfig,
    ui_test_target: str,
    flow: Flow,
    destination: str,
    run_dir: Path,
    record_video: bool,
) -> tuple[bool, Path, Optional[Path], Path]:
    result_bundle = run_dir / f"{flow.id}.xcresult"
    log_path = run_dir / f"{flow.id}.xcodebuild.log"
    video_path = run_dir / f"{flow.id}.mp4" if record_video else None
    command = build_xcodebuild_command(root, cfg, ui_test_target, flow, destination, result_bundle)
    timeout = cfg.run.build_timeout_s + cfg.run.test_timeout_s

    if video_path:
        with Recorder(destination.rsplit("=", 1)[-1], video_path):
            result = procutil.run(command, cwd=root, timeout=timeout)
    else:
        result = procutil.run(command, cwd=root, timeout=timeout)
    _write_log(log_path, result)
    return result.ok, result_bundle, video_path, log_path


def cmd_run(args: argparse.Namespace) -> int:
    root = _project_root(args.project_root)
    cfg = config.load_config(root)
    _, ui_test_target = _require_xcode_settings(cfg)
    selected = _selected_flows(cfg, args)
    delay_ms = args.delay_ms
    if delay_ms is None:
        delay_ms = cfg.run.review_speed_delay_ms if args.review else cfg.run.review_delay_ms
    sync_result = _sync(root, cfg, selected, delay_ms)
    if sync_result.get("added") or sync_result.get("removed"):
        print(
            "Synchronized generated Xcode files "
            f"(added: {', '.join(sync_result.get('added', [])) or 'none'}; "
            f"removed: {', '.join(sync_result.get('removed', [])) or 'none'})."
        )

    platform = cfg.device.platform.lower()
    record_video = not args.no_video and platform not in ("macos", "osx")
    if platform in ("macos", "osx"):
        destination = "platform=macOS"
    else:
        device = simulator.find_or_create_device(cfg.device.platform, cfg.device.name, cfg.device.os_version)
        if cfg.run.reset_simulator or args.reset_simulator:
            print(f"Erasing simulator {device.name}…")
            simulator.erase(device.udid)
        device = simulator.ensure_booted(device, cfg.run.boot_timeout_s)
        destination = f"platform=iOS Simulator,id={device.udid}"
        print(f"Using simulator: {device.name} ({device.udid})")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = paths.runs_dir(root) / timestamp
    run_dir.mkdir(parents=True, exist_ok=False)
    print(f"Running {len(selected)} flow(s); artifacts will be written to {run_dir}")

    failed: list[str] = []
    for flow in selected:
        print(f"\n→ {flow.id}: {flow.name}")
        try:
            ok, result_bundle, video_path, log_path = _run_flow(
                root, cfg, ui_test_target, flow, destination, run_dir, record_video,
            )
        except subprocess.TimeoutExpired:
            ok = False
            result_bundle = run_dir / f"{flow.id}.xcresult"
            video_path = run_dir / f"{flow.id}.mp4" if record_video else None
            log_path = run_dir / f"{flow.id}.xcodebuild.log"
            log_path.write_text("xcodebuild timed out before producing a complete log.\n")
        status = "passed" if ok else "FAILED"
        print(f"  {status}; xcresult: {result_bundle}")
        if video_path and video_path.exists():
            print(f"  video: {video_path}")
        print(f"  log: {log_path}")
        if not ok:
            failed.append(flow.id)
            if not args.keep_going:
                break

    if failed:
        print(f"\nFlow run failed: {', '.join(failed)}. Inspect its .xcresult and xcodebuild.log in {run_dir}.")
        return 1
    artifacts = "videos and Xcode result bundles" if record_video else "Xcode result bundles"
    print(f"\nAll flows passed. Review {artifacts} in {run_dir}.")
    return 0


def cmd_desktop(args: argparse.Namespace) -> int:
    """Headless Finder fallback: start a ready project, or say what is missing.

    Used only when the Flow Review macOS app could not be built/installed
    (see `install-finder`). Deliberately never opens a dialog; run
    `flow-review analyze` or the app for guided setup.
    """
    readiness = desktop.assess_project(args.project_root)
    if readiness.ready:
        desktop.launch_review_in_terminal(readiness.root)
        print(f"Started Flow Review for {readiness.root} in Terminal ({readiness.flow_count} flow(s)).")
        return 0
    print(f"Flow Review setup is needed for {readiness.root}: {readiness.reason}")
    print(f"Run `flow-review analyze {readiness.root}` to see what Flow Review can infer, "
          f"or `flow-review init {readiness.root}` to configure it.")
    return 0


def cmd_install_finder(args: argparse.Namespace) -> int:
    shim = finder.install_cli_shim(sys.executable)
    print(f"Linked the Flow Review command line core: {shim}")

    app_path: Optional[str] = None
    if args.skip_app:
        print("Skipping the macOS app build (--skip-app); Finder will use the headless fallback.")
    elif not macapp.project_available():
        print("The Flow Review macOS app project was not found in this checkout; using the headless fallback.")
    else:
        try:
            print("Building the Flow Review app (this can take a minute the first time)…")
            installed = macapp.install_app()
            app_path = str(installed)
            print(f"Installed the Flow Review app: {installed}")
        except macapp.MacAppError as exc:
            print(f"Could not build the Flow Review app, continuing with the headless fallback:\n{exc}")

    destination = finder.install_quick_action(force=args.force, app_path=app_path)
    finder.validate_quick_action(destination)
    print(f"Installed Finder Quick Action: {destination}")
    print("In Finder, right-click a project folder and choose Flow Review.")
    print("If it is not visible yet, enable “Flow Review” in System Settings → Keyboard → Keyboard Shortcuts → Services.")
    return 0


def cmd_uninstall_finder(_args: argparse.Namespace) -> int:
    if finder.uninstall_quick_action():
        print("Removed the Flow Review Finder Quick Action.")
    else:
        print("The Flow Review Finder Quick Action was not installed.")
    return 0


def cmd_ai_discover(args: argparse.Namespace) -> int:
    """Print or save review-only candidate YAML from an explicitly local model."""
    root = _project_root(args.project_root)
    cfg = config.load_config(root)
    saved = cfg.ai
    provider = args.provider or (saved.provider if saved else "lm_studio")
    base_url = args.base_url or (saved.base_url if saved else local_ai.default_base_url(provider))
    model = args.model or (saved.model if saved else None)
    settings = local_ai.LocalAISettings(provider=provider, base_url=base_url, model=model or "")
    draft = local_ai.suggest_flows(
        settings,
        cfg.name,
        cfg.device.platform,
        local_ai.collect_ui_context(root),
        timeout_s=args.timeout,
    )
    if args.output:
        output = Path(args.output).expanduser()
        if not output.is_absolute():
            output = root / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(draft + "\n")
        print(f"Saved a review-only Local AI draft to {output}")
        return 0
    print(draft)
    return 0


def cmd_ai_models(args: argparse.Namespace) -> int:
    """List models already loaded by a local OpenAI-compatible server.

    Separate from `ai-discover` because listing loaded models and asking one
    of them to draft flows are different local requests; the macOS app's
    model picker needs the former without triggering the latter.
    """
    base_url = args.base_url or local_ai.default_base_url(args.provider or "lm_studio")
    models = local_ai.list_models(base_url, timeout_s=args.timeout)
    for model in models:
        print(model)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flow-review",
        description="Generate and run reviewable XCUITest flows from YAML.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="configure an Xcode project and generated UI-test target")
    init.add_argument("project_root", nargs="?", default=".")
    init.add_argument("--name", help="human-readable project name")
    init.add_argument("--platform", default="iOS", choices=("iOS", "iPadOS", "macOS"))
    init.add_argument("--device", default="iPhone 17 Pro", help="exact installed simulator name")
    init.add_argument("--os-version", help="installed simulator runtime version, e.g. 26.5")
    init.add_argument("--scheme", help="scheme to test")
    init.add_argument("--app-target", help="app target to launch")
    init.add_argument("--ui-test-target", help="existing or new Flow Review UI-test target name")
    init.add_argument("--force", action="store_true", help="replace an existing Flow Review config")
    init.set_defaults(handler=cmd_init)

    for name, handler, help_text in (
        ("validate", cmd_validate, "validate config and YAML without changing Xcode"),
        ("list", cmd_list, "list configured flows"),
        ("doctor", cmd_doctor, "check local prerequisites and project configuration"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("project_root", nargs="?", default=".")
        command.set_defaults(handler=handler)

    analyze_cmd = subparsers.add_parser(
        "analyze", help="inspect a project and report what Flow Review can infer, without changing anything",
    )
    analyze_cmd.add_argument("project_root", nargs="?", default=".")
    analyze_cmd.add_argument("--json", action="store_true", help="machine-readable output; used by the macOS app")
    analyze_cmd.set_defaults(handler=cmd_analyze)

    run = subparsers.add_parser("run", help="generate, execute, and record one or more flows")
    run.add_argument("project_root", nargs="?", default=".")
    run.add_argument("--flow", action="append", help="flow id to run; may be repeated")
    run.add_argument("--tag", action="append", help="run flows matching any tag; may be repeated")
    run.add_argument("--review", action="store_true", help="use the slower review playback delay")
    run.add_argument("--delay-ms", type=int, help="override the delay after every step")
    run.add_argument("--no-video", action="store_true", help="do not capture simulator video")
    run.add_argument("--reset-simulator", action="store_true", help="erase the chosen simulator before running")
    run.add_argument("--keep-going", action="store_true", help="continue after a failed flow")
    run.set_defaults(handler=cmd_run)

    desktop_command = subparsers.add_parser(
        "desktop", help="open the Finder setup assistant or start a configured review",
    )
    desktop_command.add_argument("project_root", nargs="?", default=".")
    desktop_command.set_defaults(handler=cmd_desktop)

    install_finder = subparsers.add_parser(
        "install-finder", help="install a user-scoped Finder Quick Action for project folders",
    )
    install_finder.add_argument("--force", action="store_true", help="replace an existing Flow Review Quick Action")
    install_finder.add_argument(
        "--skip-app", action="store_true",
        help="do not build/install the Flow Review macOS app; use the headless CLI fallback",
    )
    install_finder.set_defaults(handler=cmd_install_finder)
    uninstall_finder = subparsers.add_parser("uninstall-finder", help="remove the Flow Review Finder Quick Action")
    uninstall_finder.set_defaults(handler=cmd_uninstall_finder)

    ai_discover = subparsers.add_parser(
        "ai-discover", help="ask an explicitly selected local model for review-only flow drafts",
    )
    ai_discover.add_argument("project_root", nargs="?", default=".")
    ai_discover.add_argument("--provider", choices=("lm_studio", "local_notebook"), help="local server preset")
    ai_discover.add_argument("--base-url", help="local OpenAI-compatible server URL, including /v1")
    ai_discover.add_argument("--model", help="loaded local model ID; use the setup assistant to list models")
    ai_discover.add_argument("--timeout", type=int, default=local_ai.DEFAULT_TIMEOUT_S, help="local model request timeout in seconds")
    ai_discover.add_argument(
        "--output",
        help="write a review-only draft here; it is never added to flow-review/flows automatically",
    )
    ai_discover.set_defaults(handler=cmd_ai_discover)

    ai_models = subparsers.add_parser(
        "ai-models", help="list models already loaded by a local OpenAI-compatible server",
    )
    ai_models.add_argument("--provider", choices=("lm_studio", "local_notebook"), help="local server preset")
    ai_models.add_argument("--base-url", help="local OpenAI-compatible server URL, including /v1")
    ai_models.add_argument("--timeout", type=int, default=local_ai.DEFAULT_TIMEOUT_S, help="request timeout in seconds")
    ai_models.set_defaults(handler=cmd_ai_models)
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return args.handler(args)
    except (
        CLIError, config.ConfigError, desktop.DesktopError, discovery.DiscoveryError,
        finder.FinderIntegrationError, FlowError, local_ai.LocalAIError,
        simulator.SimulatorError, procutil.RubyHelperError,
    ) as exc:
        print(f"flow-review: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("flow-review: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
