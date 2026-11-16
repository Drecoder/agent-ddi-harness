"""The Level 1 gauntlet, in the only order it runs: CRAP, then mutation, then the TDD pyramid."""

from dataclasses import dataclass, field


@dataclass
class GateResult:
    gate: str
    ok: bool
    summary: str
    metrics: dict = field(default_factory=dict)
    findings: list = field(default_factory=list)   # [{"subject", "message", "fix"}]
    commands: list = field(default_factory=list)   # [{"cmd", "exit", "seconds"}]: the evidence
    caveats: list = field(default_factory=list)
    seconds: float = 0.0
