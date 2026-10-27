"""Local command registry shared by the desktop UI and account tools.

Handlers receive the existing API client and account/catalog context. This module
does not load credentials, open a browser, send chat messages, or persist accounts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import json
import shlex
import threading
import time
from typing import Any, Callable, Iterable

from .diagnostic_privacy import safe_text, sanitize_report, private_literals
from .shop_rewards import api_rejected


class CommandInputError(ValueError):
    """An invalid local command argument; no request has been sent."""


class CommandUnavailable(RuntimeError):
    """A supported operation is not available in the current account state."""


@dataclass(frozen=True)
class ArgumentSpec:
    name: str
    label: str = ""
    kind: str = "text"
    required: bool = False
    default: Any = None
    choices: tuple[Any, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    description: str = ""

    def parse(self, value: Any) -> Any:
        label = self.label or self.name.replace("_", " ")
        if value is None or value == "":
            if self.required and self.default is None:
                raise CommandInputError(f"{label} is required.")
            value = self.default
            if value is None:
                return None
        if self.kind == "int":
            if isinstance(value, bool) or not re.fullmatch(r"[+-]?\d+", str(value).strip()):
                raise CommandInputError(f"{label} must be an integer.")
            parsed = int(value)
            if self.minimum is not None and parsed < self.minimum:
                raise CommandInputError(f"{label} must be at least {self.minimum}.")
            if self.maximum is not None and parsed > self.maximum:
                raise CommandInputError(f"{label} must be at most {self.maximum}.")
        elif self.kind == "bool":
            if isinstance(value, bool):
                parsed = value
            elif str(value).strip().lower() in {"1", "true", "yes", "on"}:
                parsed = True
            elif str(value).strip().lower() in {"0", "false", "no", "off"}:
                parsed = False
            else:
                raise CommandInputError(f"{label} must be yes or no.")
        elif self.kind in {"text", "choice"}:
            parsed = str(value).strip()
        else:
            raise CommandInputError(f"Unsupported argument type for {label}.")
        if self.choices and parsed not in self.choices:
            raise CommandInputError(f"{label} must be one of: {', '.join(map(str, self.choices))}.")
        return parsed


@dataclass(frozen=True)
class CommandSpec:
    name: str
    group: str
    description: str
    args: tuple[ArgumentSpec, ...] = ()
    action: bool = False
    spends_resources: bool = False
    destructive: bool = False
    sends_message: bool = False
    execution_mode: str = "read"
    aliases: tuple[str, ...] = ()
    evidence: str = ""
    usage: str = ""
    mutates: bool = False

    def __post_init__(self):
        if self.mutates or self.action:
            object.__setattr__(self, "mutates", True)
            object.__setattr__(self, "action", True)

    def parse_args(self, values: dict[str, Any] | None = None) -> dict[str, Any]:
        values = values or {}
        unknown = set(values) - {argument.name for argument in self.args}
        if unknown:
            # Do not echo arbitrary user-supplied keys/values into logs.
            raise CommandInputError("The command received an unsupported argument.")
        return {argument.name: argument.parse(values.get(argument.name)) for argument in self.args}


@dataclass
class CommandResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    details: str = ""
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    title: str = ""

    @property
    def detail(self) -> str:
        return self.details

    @detail.setter
    def detail(self, value: str):
        self.details = value

    def safe(self, literals=()) -> "CommandResult":
        """Return normalized output safe for the local result panel and logs."""
        return CommandResult(rows=sanitize_report(self.rows, literals), details=safe_text(self.details, literals),
                             message=safe_text(self.message, literals), data=sanitize_report(self.data, literals),
                             title=safe_text(self.title, literals))


def command_key(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip().lstrip("!/").lower())


def parse_argument_text(spec: CommandSpec, text: str) -> dict[str, Any]:
    """Accept a JSON object, named key=value fields, or quoted positional args."""
    text = str(text or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            values = json.loads(text)
        except ValueError:
            raise CommandInputError("Arguments must be a valid JSON object.") from None
        if not isinstance(values, dict):
            raise CommandInputError("Arguments must be a JSON object.")
        return values
    try:
        tokens = shlex.split(text)
    except ValueError:
        raise CommandInputError("An argument has unmatched quotes.") from None
    values = {}; positional = 0
    names = [argument.name for argument in spec.args]
    for token in tokens:
        if "=" in token:
            name, value = token.split("=", 1)
            name = name.lstrip("-")
            if name not in names or name in values:
                raise CommandInputError("An argument is unknown or duplicated.")
        else:
            while positional < len(names) and names[positional] in values:
                positional += 1
            if positional >= len(names):
                raise CommandInputError("The command received too many arguments.")
            name, value = names[positional], token
            positional += 1
        values[name] = value
    return values


class CommandContext:
    def __init__(self, client: Any, *, snapshot: dict[str, Any] | None = None,
                 snapshot_loader: Callable[[], dict[str, Any]] | None = None,
                 catalogs: dict[str, dict[str, Any]] | None = None,
                 emit: Callable[[dict[str, Any]], None] | None = None,
                 clock: Callable[[], float] = time.time):
        self.client = client
        self._snapshot = snapshot
        self._snapshot_loader = snapshot_loader or client.init
        self._catalogs = {str(key).removesuffix("_data"): value for key, value in (catalogs or {}).items()}
        self.emit = emit or (lambda _event: None)
        self.clock = clock
        self._snapshot_at = clock() if snapshot is not None else 0
        self._full_at = self._snapshot_at
        self.action_depth = 0
        self._read_interval = max(0.0, float(getattr(client, "command_read_interval", 0)))
        self._last_read = None
        self.private = private_literals({"credentials": vars(client.credentials)}) if getattr(client, "credentials", None) else set()

    def snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        # Read panels share a recent account snapshot. Resource-changing
        # commands always invalidate it before starting and may force refresh.
        if not self.action_depth and self._snapshot is not None and self.clock()-self._snapshot_at>30 and self.clock()-self._full_at<=300 and hasattr(self.client,"get_user_account"):
            compact=self.call("getUserAccount")
            merged={**self._snapshot,**{k:v for k,v in compact.items() if k not in {"request","result","result_message"}}}
            for key in ("user_data","common_fields"):
                merged[key]={**(self._snapshot.get(key)or{}),**(compact.get(key)or{})}
            self._snapshot=merged;self._snapshot_at=self.clock()
        if refresh and self.action_depth or self._snapshot is None or self.clock() - self._snapshot_at > 30:
            self._snapshot = self._load_snapshot()
            for attempt in range(3):
                if not self._rate_limited(self._snapshot):break
                time.sleep(8 * (attempt + 1))
                self._snapshot = self._load_snapshot()
            self._snapshot_at = self.clock()
            self._full_at = self._snapshot_at
        if not isinstance(self._snapshot, dict) or not self._snapshot or api_rejected(self._snapshot):
            raise CommandUnavailable("The live account could not be read.")
        self.private.update(private_literals(self._snapshot))
        return self._snapshot

    def catalog(self, name: str) -> dict[str, Any]:
        name = str(name).removesuffix("_data")
        if name not in self._catalogs:
            loader = getattr(self.client, f"{name}_catalog", None)
            if loader is None:
                # Account catalogs are authoritative when no dedicated static
                # helper exists, and never trigger speculative endpoint calls.
                raw = self.snapshot().get(f"{name}_data") or {}
                self._catalogs[name] = raw if isinstance(raw, dict) else {}
            else:
                self._catalogs[name] = loader()
        return self._catalogs[name]

    def call(self, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        is_read = message.startswith("get") or message == "init"
        if is_read:self._pace_read()
        response = self.client.call(message, data)
        # Repeating an explicit rejected read is safe. Mutating requests,
        # ambiguous timeouts and purchases are never retried by this context.
        if is_read:
            for attempt in range(3):
                if not self._rate_limited(response):break
                time.sleep(8 * (attempt + 1))
                self._pace_read()
                response = self.client.call(message, data)
        self.private.update(private_literals(response))
        if not response and not (message.startswith("get") or message=="init"):
            raise CommandUnavailable("AT returned no action confirmation. Its outcome is unknown; no request was retried.")
        if api_rejected(response):
            text = response.get("result_message") if isinstance(response, dict) else None
            text = " ".join(map(str, text)) if isinstance(text, list) else str(text or "AT rejected the request.")
            raise CommandUnavailable(safe_text(text, self.private)[:500])
        return response

    def invalidate(self) -> None:
        self._snapshot = None

    def _pace_read(self):
        if not self._read_interval:return
        if self._last_read is not None:
            delay = self._read_interval - (time.monotonic() - self._last_read)
            if delay > 0:time.sleep(delay)
        self._last_read = time.monotonic()

    def _load_snapshot(self):
        self._pace_read()
        return self._snapshot_loader()

    @staticmethod
    def _rate_limited(response):
        if not api_rejected(response):return False
        text=str(response.get("result_message", "")).lower() if isinstance(response,dict) else ""
        return "warning_16" in text or "try that again in a moment" in text or "too fast" in text


Handler = Callable[["LocalCommandService", dict[str, Any]], CommandResult]


class LocalCommandService:
    def __init__(self, context: CommandContext | Any, **context_options):
        self.context = context if isinstance(context, CommandContext) else CommandContext(context, **context_options)
        self._specs: dict[str, CommandSpec] = {}
        self._aliases: dict[str, str] = {}
        self._handlers: dict[str, Handler] = {}
        self._lock = threading.RLock()

    @property
    def client(self):
        return self.context.client

    @property
    def clock(self):
        return self.context.clock

    @property
    def emit(self):
        return self.context.emit

    def snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        return self.context.snapshot(refresh=refresh)

    def catalog(self, name: str) -> dict[str, Any]:
        return self.context.catalog(name)

    def call(self, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.context.call(message, data)

    def register(self, spec: CommandSpec, handler: Handler) -> None:
        key = command_key(spec.name)
        aliases = {key, *(command_key(alias) for alias in spec.aliases)}
        if not key or any(alias in self._aliases for alias in aliases):
            raise ValueError("A command name or alias is already registered.")
        if spec.execution_mode not in {"read", "action", "runner"}:
            raise ValueError("Unsupported local command execution mode.")
        if spec.spends_resources or spec.destructive or spec.sends_message:
            if not spec.action:
                raise ValueError("Mutating command flags require action=True.")
        self._specs[key] = spec; self._handlers[key] = handler
        for alias in aliases:
            self._aliases[alias] = key

    def specs(self, group: str | None = None) -> list[CommandSpec]:
        return sorted((spec for spec in self._specs.values() if group is None or spec.group.lower() == group.lower()),
                      key=lambda spec: (spec.group.lower(), spec.name.lower()))

    def groups(self) -> list[str]:
        return sorted({spec.group for spec in self._specs.values()})

    def spec(self, name: str) -> CommandSpec:
        canonical = self._aliases.get(command_key(name))
        if canonical is None:
            raise CommandInputError("That local command is not registered.")
        return self._specs[canonical]

    def execute(self, name: str, args: dict[str, Any] | str | None = None) -> CommandResult:
        spec = self.spec(name)
        if isinstance(args, str):
            args = parse_argument_text(spec, args)
        if spec.name=="play":
            defaults=getattr(self,"store",None)
            saved=defaults.read().get("play_defaults",{}) if defaults else {}
            args={**saved,**(args or {})}
        values = spec.parse_args(args)
        self.context.private.update(private_literals(values))
        with self._lock:
            if spec.action:
                self.context.invalidate()
                self.context.action_depth += 1
            try:
                result = self._handlers[command_key(spec.name)](self, values)
                if not isinstance(result, CommandResult):
                    raise TypeError("Command handlers must return CommandResult.")
            finally:
                if spec.action:
                    self.context.action_depth -= 1
                    self.context.invalidate()
            result.title = result.title or spec.name
            return result.safe(self.context.private)


def register_commands(service: LocalCommandService, registrations: Iterable[tuple[CommandSpec, Handler]]) -> None:
    for spec, handler in registrations:
        service.register(spec, handler)
