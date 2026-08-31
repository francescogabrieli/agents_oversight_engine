from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime
from typing import Any
from urllib.parse import urlparse, urlsplit

from layer1_intake import TaskSpec
from layer2_runtime_governance.actor_contract import ActorResultType
from layer2_runtime_governance.domain import (
    TaskState,
    build_evidence_selection_digest,
    build_evidence_snapshot_manifest,
    build_observation_lineage,
    derive_task_evidence_floor,
    digest_json,
    lift_evidence_burden_to_floor,
)
from layer2_runtime_governance.domain.step_recovery import resolve_evidence_episode_id
from layer3_tool_bus_external_capabilities.external_operations import (
    CommunicationOperationPayload,
)
from layer3_tool_bus_external_capabilities.tool_contract import (
    RetrievalKind,
    ToolName,
    ToolObservation,
    canonical_web_locator,
)

_GENERAL_NUMBER_PATTERN = re.compile(
    r"(?<![\w-])(?:\d+\.\d+|\d{3,})(?![\w-])"
)
TOKEN_PATTERNS = [
    # URLs
    re.compile(r"https?://[^\s<>\"'()]+"),
    # Emails
    re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
    # Currency amounts
    re.compile(r"(?:[\$€£¥]\s*\d+(?:[\.,]\d+)?|\b\d+(?:[\.,]\d+)?\s*(?:USD|EUR|GBP|JPY|CHF)\b)"),
    # Percentages
    re.compile(r"\b\d+(?:[\.,]\d+)?%"),
    # Dates (ISO or slash format)
    re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b"),
    # Numbers with decimals or >= 3 digits
    _GENERAL_NUMBER_PATTERN,
]
_STRUCTURAL_NUMBER_PREFIX = re.compile(
    r"(?:articolo|capitolo|fonte|sezione|step)\s+$",
    re.IGNORECASE,
)
_STRUCTURAL_NUMBER_LINE_START = re.compile(
    r"^\s*\d+(?:\.\d+)*[.)]\s"
)
# prefix: currency symbol; suffix: percent sign or ISO currency code.
_NUMERIC_LITERAL = re.compile(
    r"^(?P<prefix>[\$€£¥])?\s*(?P<number>\d[\d.,\s ]*\d|\d)"
    r"\s*(?P<suffix>%|USD|EUR|GBP|JPY|CHF)?$",
    re.IGNORECASE,
)
# "141.006" and "1,234,567": a separator followed by exactly three digits, repeated.
_GROUPED_INTEGER = re.compile(r"^\d{1,3}(?P<sep>[.,])\d{3}(?:(?P=sep)\d{3})*$")

# A task states a date in prose ("24 agosto 2026"); an agent renders it numerically.
# Both denote the same authoritative value, so the corpus index must carry every
# rendering a literal token can take.
_MONTH_NAMES = {
    "gennaio": 1, "january": 1, "jan": 1,
    "febbraio": 2, "february": 2, "feb": 2,
    "marzo": 3, "march": 3, "mar": 3,
    "aprile": 4, "april": 4, "apr": 4,
    "maggio": 5, "may": 5,
    "giugno": 6, "june": 6, "jun": 6,
    "luglio": 7, "july": 7, "jul": 7,
    "agosto": 8, "august": 8, "aug": 8,
    "settembre": 9, "september": 9, "sep": 9, "sept": 9,
    "ottobre": 10, "october": 10, "oct": 10,
    "novembre": 11, "november": 11, "nov": 11,
    "dicembre": 12, "december": 12, "dec": 12,
}
_MONTH_NAME_ALTERNATION = "|".join(sorted(_MONTH_NAMES, key=len, reverse=True))
_PROSE_DATE_PATTERNS = (
    # "24 agosto 2026" / "24 August 2026"
    re.compile(
        rf"\b(?P<day>\d{{1,2}})\s+(?P<month>{_MONTH_NAME_ALTERNATION})\.?\s+(?P<year>\d{{4}})\b",
        re.IGNORECASE,
    ),
    # "August 24, 2026"
    re.compile(
        rf"\b(?P<month>{_MONTH_NAME_ALTERNATION})\.?\s+(?P<day>\d{{1,2}}),?\s+(?P<year>\d{{4}})\b",
        re.IGNORECASE,
    ),
)

# A source states a share in prose ("28 percent", "3,5 per cento"); an agent renders
# it with the symbol ("28%"). Both denote the same quantity, so the prose form has to
# expand into the same canonical keys a "%"-suffixed token would produce.
_PROSE_PERCENT_PATTERN = re.compile(
    r"\b(?P<number>\d+(?:[.,]\d+)?)\s*(?:percent|per\s*cento|percento)\b",
    re.IGNORECASE,
)


_ACTOR_DERIVED_TOOL_NAMES = frozenset(
    {
        ToolName.MCP_LOCAL_TEST_ECHO.value,
        ToolName.MCP_LOCAL_TEST_TEXT_STATS.value,
    }
)
_SELF_PRODUCED_LOCATOR_PREFIXES = (
    "actor-output://",
    "agent-output://",
    "draft://",
    "runtime-artifact://",
    "scratchpad://",
    "workspace://",
)
# The declaration is recognized per sentence and is order-insensitive: an author may
# write "declare the insufficiency" or "the sources are insufficient, stated explicitly".
# A sentence must name the sources and assert their shortfall; a conditional clause that
# merely restates the task rule ("if fewer than three sources, declare it") is not a
# declaration and is excluded.
_SENTENCE_SPLIT = re.compile(r"[.!?\n]+")
_INSUFFICIENCY_SOURCE_TERM = re.compile(r"\b(?:fonti|sources?)\b", re.IGNORECASE)
_INSUFFICIENCY_SHORTFALL = re.compile(
    r"\b(?:insufficien\w*|non\s+sufficient\w*|not\s+enough|"
    r"(?:fewer|less)\s+than\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)|"
    r"(?:meno|inferiore|inferiori)\s+(?:di|a|alle|ai)?\s*(?:\d+|uno|due|tre|quattro|"
    r"cinque|sei|sette|otto|nove|dieci)|"
    r"(?:solo|soltanto|solamente|only)\s+(?:\d+|uno|una|due|tre|one|two|three))\b",
    re.IGNORECASE,
)
_INSUFFICIENCY_CONDITIONAL = re.compile(
    r"^\W*(?:se|qualora|nel\s+caso|if|when|where)\b", re.IGNORECASE
)


# Detect an explicit, assertive statement that the valid sources fall short.
def _declares_insufficiency(target_text: str) -> bool:
    for sentence in _SENTENCE_SPLIT.split(target_text):
        if _INSUFFICIENCY_CONDITIONAL.match(sentence):
            continue
        if _INSUFFICIENCY_SOURCE_TERM.search(
            sentence
        ) and _INSUFFICIENCY_SHORTFALL.search(sentence):
            return True
    return False


def extract_source_origin(locator: str | None) -> str:
    if not locator:
        return "unknown_origin"
    parsed = urlparse(locator)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc.lower()}"
    parts = locator.split(":", 1)
    if len(parts) == 2:
        return f"{parts[0].lower()}:{parts[1].split('/')[0].lower()}"
    return locator.lower().strip()


# Exclude document structure labels such as "Articolo 1.1" from factual literals.
def _is_structural_number(*, text: str, start: int) -> bool:
    prefix = text[max(0, start - 32):start]
    line_start = text.rfind("\n", 0, start) + 1
    line_prefix = text[line_start:start]
    # Prefer a false negative when a number begins a list or section heading.
    return (
        _STRUCTURAL_NUMBER_PREFIX.search(prefix) is not None
        or _STRUCTURAL_NUMBER_LINE_START.match(line_prefix) is not None
    )


def _extract_literal_tokens(text: str) -> list[str]:
    if not text:
        return []
    tokens: set[str] = set()
    for pat in TOKEN_PATTERNS:
        for match in pat.finditer(text):
            if pat is _GENERAL_NUMBER_PATTERN and _is_structural_number(
                text=text,
                start=match.start(),
            ):
                continue
            tok = match.group(0).strip()
            if tok:
                tokens.add(tok)
    return sorted(tokens)


# A number denotes the same quantity whichever locale renders it. An Italian agent
# writes "141.006" and "30,4%" for the "141,006" and "30.4%" of an English source, and
# "97%" for its "97.0%". Comparing surface forms reported those as unsupported claims,
# so anchoring compares quantities: a literal yields every canonical key its notation
# can plausibly denote, and it is anchored when the corpus offers any of the same keys.
def _literal_comparison_keys(token: str) -> frozenset[str]:
    numeric = _NUMERIC_LITERAL.fullmatch(token.strip())
    if numeric is None:
        return frozenset({token})
    prefix = numeric.group("prefix") or ""
    suffix = (numeric.group("suffix") or "").strip().upper()
    keys = {
        f"{prefix}{value}{suffix}"
        for value in _numeric_readings(numeric.group("number"))
    }
    return frozenset(keys) if keys else frozenset({token})


# Yield every quantity a separator layout can denote, canonically formatted.
def _numeric_readings(text: str) -> set[str]:
    digits = re.sub(r"[\s ]", "", text)
    if digits.isdigit():
        return _decimal_key(digits)
    commas = digits.count(",")
    dots = digits.count(".")
    readings: set[str] = set()
    if commas and dots:
        # The rightmost separator is the decimal point; the other one groups thousands.
        if digits.rfind(",") > digits.rfind("."):
            readings |= _decimal_key(digits.replace(".", "").replace(",", "."))
        else:
            readings |= _decimal_key(digits.replace(",", ""))
        return readings
    separator = "," if commas else "."
    if commas + dots > 1:
        # Repeated separators can only group thousands.
        return _decimal_key(digits.replace(separator, ""))
    if _GROUPED_INTEGER.fullmatch(digits):
        # A lone separator before exactly three digits is genuinely ambiguous: it groups
        # thousands in one locale and opens decimals in the other. Both readings are
        # admitted, because this family exists to catch invented values, not to punish
        # an agent for the notation of its own language.
        readings |= _decimal_key(digits.replace(separator, ""))
    readings |= _decimal_key(digits.replace(separator, "."))
    return readings


# Render one canonical decimal string, dropping trailing zeros so 97 == 97.0.
def _decimal_key(text: str) -> set[str]:
    try:
        value = Decimal(text)
    except InvalidOperation:
        return set()
    # Decimal.normalize renders integers in exponent form ("1E+3"); expand them back.
    return {format(value.normalize(), "f")}


# Include immutable observation provenance when checking literal claim anchoring.
def _literal_evidence_corpus(observations: list[ToolObservation]) -> str:
    parts: list[str] = []
    for item in observations:
        parts.append(item.content)
        if item.locator:
            parts.append(item.locator)
        source_timestamp = _source_timestamp_text(
            getattr(item, "source_timestamp", None)
        )
        if source_timestamp:
            parts.append(source_timestamp)
    return " ".join(parts)


# Build the same URL and literal identity index for task and observation corpora.
@dataclass(frozen=True)
class _LiteralCorpusIndex:
    locators: frozenset[str]
    corpus_urls: frozenset[str]
    parsed_urls: tuple[object, ...]
    origins: frozenset[str]
    literal_keys: frozenset[str]
    corpus_text: str


# Build a task corpus without materializing intake text as a tool observation.
def _task_literal_corpus(task_spec: TaskSpec) -> str:
    return " ".join(
        [
            task_spec.goal,
            task_spec.context,
            *task_spec.constraints,
            *task_spec.success_criteria,
        ]
    )


# Expand every prose date into the numeric renderings a literal token can use, so a
# date stated authoritatively by the task is recognized however the agent formats it.
def _prose_date_renderings(corpus: str) -> set[str]:
    renderings: set[str] = set()
    for pattern in _PROSE_DATE_PATTERNS:
        for match in pattern.finditer(corpus):
            month = _MONTH_NAMES[match.group("month").casefold()]
            day = int(match.group("day"))
            year = int(match.group("year"))
            if not 1 <= day <= 31:
                continue
            renderings.update(
                {
                    f"{year:04d}-{month:02d}-{day:02d}",
                    f"{year:04d}/{month:02d}/{day:02d}",
                    f"{day:02d}/{month:02d}/{year:04d}",
                    f"{day:02d}-{month:02d}-{year:04d}",
                    f"{day}/{month}/{year}",
                    f"{day}-{month}-{year}",
                }
            )
    return renderings


# Mirror _prose_date_renderings for the word-form of a percentage, so "28 percent" and
# "28%" anchor to each other exactly as "141.006" and "141,006" already do by quantity.
def _prose_percent_renderings(corpus: str) -> set[str]:
    renderings: set[str] = set()
    for match in _PROSE_PERCENT_PATTERN.finditer(corpus):
        renderings.update(
            f"{value}%" for value in _numeric_readings(match.group("number"))
        )
    return renderings


# Normalize URL identity and non-URL literal keys once for both anchoring domains.
def _build_literal_corpus_index(
    *,
    corpus: str,
    observations: list[ToolObservation] = (),
) -> _LiteralCorpusIndex:
    locator_values = {
        canonical_web_locator(item.locator)
        for item in observations
        if item.locator
        and item.locator.casefold().startswith(("http://", "https://"))
    }
    corpus_urls = {
        canonical_web_locator(item)
        for item in _extract_literal_tokens(corpus)
        if item.casefold().startswith(("http://", "https://"))
    }
    parsed_urls = tuple(
        urlsplit(locator) for locator in locator_values | corpus_urls
    )
    origins = frozenset(
        f"{parsed.scheme}://{parsed.netloc.lower()}"
        for parsed in parsed_urls
        if parsed.scheme and parsed.netloc
    )
    literal_keys = frozenset(
        {
            key
            for item in _extract_literal_tokens(corpus)
            if not item.casefold().startswith(("http://", "https://"))
            for key in _literal_comparison_keys(item)
        }
        | _prose_date_renderings(corpus)
        | _prose_percent_renderings(corpus)
    )
    return _LiteralCorpusIndex(
        locators=frozenset(locator_values),
        corpus_urls=frozenset(corpus_urls),
        parsed_urls=parsed_urls,
        origins=origins,
        literal_keys=literal_keys,
        corpus_text=corpus,
    )


# Admit prose punctuation only when its stripped URL is an exact observed locator.
def _literal_is_anchored(token: str, index: _LiteralCorpusIndex) -> bool:
    if token.casefold().startswith(("http://", "https://")):
        canonical_token = canonical_web_locator(token)
        if canonical_token in index.locators or canonical_token in index.corpus_urls:
            return True
        parsed_token = urlsplit(canonical_token)
        token_origin = f"{parsed_token.scheme}://{parsed_token.netloc.lower()}"
        if (
            parsed_token.path in {"", "/"}
            and not parsed_token.query
            and token_origin in index.origins
        ):
            return True
        token_path = parsed_token.path.rstrip("/")
        if token_path and not parsed_token.query and any(
            observed.scheme == parsed_token.scheme
            and observed.netloc.lower() == parsed_token.netloc.lower()
            and (
                observed.path.rstrip("/") == token_path
                or observed.path.startswith(f"{token_path}/")
            )
            for observed in index.parsed_urls
        ):
            return True
        stripped = token.rstrip(".,")
        return stripped != token and canonical_web_locator(stripped) in index.locators
    return token in index.corpus_text or bool(
        _literal_comparison_keys(token) & index.literal_keys
    )


# Return task-anchored and truly unanchored literals in stable target-token order.
def _unanchored_literal_tokens(
    *,
    target_tokens: list[str],
    observations: list[ToolObservation],
    task_corpus: str,
) -> tuple[list[str], list[str]]:
    task_index = _build_literal_corpus_index(corpus=task_corpus)
    observation_index = _build_literal_corpus_index(
        corpus=_literal_evidence_corpus(observations),
        observations=observations,
    )
    task_anchored: list[str] = []
    unanchored: list[str] = []
    for token in target_tokens:
        if _literal_is_anchored(token, task_index):
            task_anchored.append(token)
            continue
        if not _literal_is_anchored(token, observation_index):
            unanchored.append(token)
    return task_anchored, unanchored


# A token that names a source is not the same kind of claim as one that states a
# quantity. An unanchored URL is resolvable by retrieval - the actor fetches what it
# cited, or drops a link that does not exist. An unanchored number, date or percentage
# is not: no fetch turns a computed value into a quoted one, and the two must therefore
# reach different remediation paths.
def _partition_locator_tokens(tokens: list[str]) -> tuple[list[str], list[str]]:
    locators = [
        token for token in tokens
        if token.casefold().startswith(("http://", "https://"))
    ]
    values = [
        token for token in tokens
        if not token.casefold().startswith(("http://", "https://"))
    ]
    return locators, values


# A reviewer cannot judge a flagged value from the value alone: "9/07/2026" says
# nothing, the sentence containing it says everything. The pillar already holds both the
# examined text and the exact tokens it rejected, so it is the only component that can
# state what was flagged and where without guessing.
#
# Knowing what the agent wrote is still not enough to decide: the reviewer has to know
# whether the same quantity exists in the sources in some form the matcher missed, or
# is absent entirely. So each flagged value is also looked up in the selected sources by
# its canonical quantity - a deterministic search, not a nearest-match guess. A hit is
# reported as a passage to check, never as "the source said X", and an explicit absence
# is itself the decisive signal.
_EXCERPT_RADIUS = 110
_MAX_SOURCE_MATCHES = 3


def _literal_excerpts(
    *,
    target_text: str,
    tokens: list[str],
    observations: list[ToolObservation],
) -> list[dict[str, Any]]:
    excerpts: list[dict[str, Any]] = []
    for token in tokens:
        index = target_text.find(token)
        if index < 0:
            excerpt = ""
        else:
            start = max(0, index - _EXCERPT_RADIUS)
            end = min(len(target_text), index + len(token) + _EXCERPT_RADIUS)
            fragment = " ".join(target_text[start:end].split())
            excerpt = (
                f"{'...' if start > 0 else ''}{fragment}{'...' if end < len(target_text) else ''}"
            )
        excerpts.append(
            {
                "value": token,
                "excerpt": excerpt,
                "source_matches": _source_matches_for_literal(
                    token=token,
                    observations=observations,
                ),
            }
        )
    return excerpts


# Render the surface forms one canonical quantity can take, so a source writing "82.5"
# is found for an agent that wrote "82,5%". Non-numeric tokens are searched verbatim.
def _literal_search_variants(token: str) -> list[str]:
    numeric = _NUMERIC_LITERAL.fullmatch(token.strip())
    if numeric is None:
        return [token]
    variants: set[str] = {token}
    for reading in _numeric_readings(numeric.group("number")):
        variants.add(reading)
        variants.add(reading.replace(".", ","))
    return sorted(variants, key=len, reverse=True)


# Locate one flagged value inside the sources the actor actually selected.
def _source_matches_for_literal(
    *,
    token: str,
    observations: list[ToolObservation],
) -> list[dict[str, str]]:
    matches: list[dict[str, str]] = []
    for observation in observations:
        content = observation.content or ""
        if not content:
            continue
        for variant in _literal_search_variants(token):
            index = content.find(variant)
            if index < 0:
                continue
            start = max(0, index - _EXCERPT_RADIUS)
            end = min(len(content), index + len(variant) + _EXCERPT_RADIUS)
            fragment = " ".join(content[start:end].split())
            matches.append(
                {
                    "locator": observation.locator or "",
                    "excerpt": (
                        f"{'...' if start > 0 else ''}{fragment}{'...' if end < len(content) else ''}"
                    ),
                }
            )
            break
        if len(matches) >= _MAX_SOURCE_MATCHES:
            break
    return matches


def _get_target_text(task_state: TaskState) -> str:
    parts: list[str] = []
    if task_state.external_operation_proposal is not None:
        payload = task_state.external_operation_proposal.payload
        # Only assertions are anchored. A communication body claims things about the
        # world; a SQL statement asks the world a question, and its literals are
        # selection criteria the actor authored, not values a source has to support.
        if isinstance(payload, CommunicationOperationPayload) and payload.body:
            parts.append(payload.body)
    if (
        task_state.external_communication is not None
        and task_state.external_communication.draft is not None
        and task_state.external_communication.draft.body
    ):
        parts.append(task_state.external_communication.draft.body)
    if task_state.actor_result is not None and task_state.actor_result.output_text:
        parts.append(task_state.actor_result.output_text)
    return " ".join(parts)


# Carry an observed publication timestamp into the literal corpus as source provenance.
def _source_timestamp_text(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


# Reject typed actor-produced artifacts and echo-style transformations as evidence sources.
def _detect_circular_evidence(
    *,
    selected: list[ToolObservation],
    observation_lineage: dict[str, Any],
) -> dict[str, str]:
    circular_reasons: dict[str, str] = {}
    for item in selected:
        locator = (item.locator or "").lower()
        if any(locator.startswith(prefix) for prefix in _SELF_PRODUCED_LOCATOR_PREFIXES):
            circular_reasons[item.observation_id] = "self_produced_locator"
            continue
        lineage = observation_lineage.get(item.observation_id)
        if (
            lineage is not None
            and lineage.tool_name in _ACTOR_DERIVED_TOOL_NAMES
        ):
            circular_reasons[item.observation_id] = "actor_derived_tool_output"
    return circular_reasons


# Build deterministic evidence facts across the 5 structural families.
def build_evidence_signal(
    *,
    task_state: TaskState,
    thresholds_version: str,
) -> dict[str, Any]:
    result = task_state.actor_result
    validation = result.step_validation if result is not None else None
    selected_refs = validation.evidence_refs if validation is not None else ()
    evidence_by_id = {
        item.observation_id: item for item in task_state.observations
    }
    selected = [evidence_by_id[item] for item in selected_refs if item in evidence_by_id]
    current_step = task_state.current_plan_step
    produces_factual_claims = bool(
        current_step and getattr(current_step, "produces_factual_claims", False)
    )
    produces_external_effect_proposal = bool(
        current_step
        and getattr(current_step, "produces_governed_external_effect_proposal", False)
    )
    evidence_families_active = (
        produces_factual_claims or produces_external_effect_proposal
    )
    completion_attempt = bool(
        result and result.result_type == ActorResultType.STEP_COMPLETED
    )
    factual_completion_attempt = completion_attempt and produces_factual_claims
    evidence_completion_attempt = completion_attempt and evidence_families_active
    unique_selected = tuple(dict.fromkeys(selected_refs))
    available_obs_ids = {item.observation_id for item in task_state.observations}
    observation_lineage = build_observation_lineage(task_state=task_state)

    # Famiglia 1: Integrità referenziale e digest binding
    selected_evidence_refs_valid = len(selected) == len(selected_refs)
    selected_evidence_refs_unique = len(unique_selected) == len(selected_refs)
    observation_ids_unique = len(available_obs_ids) == len(task_state.observations)
    selected_observations_have_content = all(
        bool(item.content and item.content.strip()) for item in selected
    ) if selected else True

    selected_known_step_ids = [
        observation_lineage[item.observation_id].plan_step_id
        for item in selected
        if item.observation_id in observation_lineage
    ]
    known_step_ids = (
        {step.step_id for step in task_state.plan_steps}
        | {step.step_id for step in task_state.executed_steps}
        | {res.request.plan_step_id for res in task_state.tool_results_history}
    )
    provenance_resolvable = all(
        item.observation_id in observation_lineage
        for item in selected
    )
    known_steps = provenance_resolvable and all(
        step_id in known_step_ids for step_id in selected_known_step_ids
    )

    snapshot_manifest = build_evidence_snapshot_manifest(
        task_state=task_state,
        observation_lineage=observation_lineage,
    )
    snapshot_digest = digest_json(snapshot_manifest)
    external_operation_digest = (
        task_state.external_operation_proposal.payload_digest
        if task_state.external_operation_proposal is not None
        else None
    )
    selection_digest = build_evidence_selection_digest(
        selected_refs=tuple(selected_refs),
        evidence_snapshot_digest=snapshot_digest,
        external_operation_digest=external_operation_digest,
    )
    completion_candidate = task_state.pending_completion_candidate
    candidate_binding_required = completion_attempt
    candidate_binding_present = bool(
        completion_candidate is not None
        and completion_candidate.evidence_snapshot_digest
        and completion_candidate.evidence_selection_digest
    )
    candidate_snapshot_matches = bool(
        candidate_binding_present
        and completion_candidate is not None
        and completion_candidate.evidence_snapshot_digest == snapshot_digest
    )
    candidate_selection_matches = bool(
        candidate_binding_present
        and completion_candidate is not None
        and completion_candidate.evidence_selection_digest == selection_digest
    )
    candidate_operation_matches = bool(
        completion_candidate is not None
        and completion_candidate.external_operation_digest
        == external_operation_digest
    )

    # Famiglia 2: Copertura dell'onere probatorio dichiarato (Fail-Closed)
    declared_evidence_burden = (
        getattr(current_step, "evidence_burden", None) if current_step else None
    )
    task_evidence_floor = derive_task_evidence_floor(task_state.task_spec)
    evidence_burden = lift_evidence_burden_to_floor(
        declared_evidence_burden,
        task_evidence_floor if evidence_families_active else None,
    )
    factual_step_missing_evidence_burden = (
        produces_factual_claims and declared_evidence_burden is None
    )

    # The declared burden belongs to the claim, so coverage is measured on the
    # actor-selected references. The retrieved universe stays a separate diagnostic:
    # it distinguishes "the run never found the sources" from "the actor did not cite
    # the sources it already had", which are different remediations.
    # The pre-turn actor gate can only observe the universe, and is therefore strictly
    # weaker than this check: a universe below the minimum implies a selection below it.
    retrieved_universe = [
        item
        for item in task_state.observations
        if item.retrieval_kind != RetrievalKind.SEARCH_SNIPPET
    ]
    retrieved_independent_sources_count = len(
        {
            extract_source_origin(item.locator)
            for item in retrieved_universe
            if item.locator
        }
    )
    selected_retrieved = [
        item
        for item in selected
        if item.retrieval_kind != RetrievalKind.SEARCH_SNIPPET
    ]
    distinct_origins = {
        extract_source_origin(item.locator)
        for item in selected_retrieved
        if item.locator
    }
    distinct_independent_sources_count = len(distinct_origins)

    search_title_only_ref_count = sum(
        1
        for item in selected
        if item.retrieval_kind == RetrievalKind.SEARCH_SNIPPET
    )

    if evidence_families_active and evidence_burden is not None:
        coverage_evaluated = True
        min_sources = evidence_burden.min_independent_sources
        independent_sources_satisfied = (
            distinct_independent_sources_count >= min_sources
        )
        if evidence_burden.require_retrieved_content:
            retrieved_content_satisfied = bool(selected_retrieved)
        else:
            retrieved_content_satisfied = True
        burden_satisfied = (
            independent_sources_satisfied and retrieved_content_satisfied
        )
    elif produces_factual_claims:
        # Legacy or malformed state must not silently downgrade the factual obligation.
        coverage_evaluated = False
        min_sources = 0
        independent_sources_satisfied = False
        retrieved_content_satisfied = False
        burden_satisfied = False
    else:
        # An external proposal may activate the families without declaring a burden.
        coverage_evaluated = False
        min_sources = 0
        independent_sources_satisfied = True
        retrieved_content_satisfied = True
        burden_satisfied = True

    # Famiglia 3: Qualità strutturale delle osservazioni
    selected_evidence_all_title_only = bool(selected) and search_title_only_ref_count == len(selected)

    # Famiglia 4: Non-circolarità
    circular_evidence_reasons = _detect_circular_evidence(
        selected=selected,
        observation_lineage=observation_lineage,
    )
    circular_reference_detected = bool(circular_evidence_reasons)

    # Famiglia 5: Ancoraggio letterale dei claim (solo token estraibili)
    target_text = _get_target_text(task_state)
    task_corpus = _task_literal_corpus(task_state.task_spec)
    target_tokens = _extract_literal_tokens(target_text)
    literals_anchored_to_task, unanchored_in_selection = _unanchored_literal_tokens(
        target_tokens=target_tokens,
        observations=selected,
        task_corpus=task_corpus,
    )
    _, unanchored_in_universe = _unanchored_literal_tokens(
        target_tokens=target_tokens,
        observations=task_state.observations,
        task_corpus=task_corpus,
    )
    (
        unanchored_locators_in_selection,
        unanchored_literals_in_selection,
    ) = _partition_locator_tokens(unanchored_in_selection)
    (
        unanchored_locators_in_universe,
        unanchored_literals_in_universe,
    ) = _partition_locator_tokens(unanchored_in_universe)
    # A human who has already ruled on exactly this selection settles the question. The
    # binding is the selection digest, so the ruling cannot carry over to a different
    # completion, and an unreviewed concern still reaches a person.
    adjudicated_anchoring_digest = getattr(
        task_state,
        "adjudicated_evidence_anchoring_digest",
        None,
    )
    anchoring_concern_adjudicated = bool(
        adjudicated_anchoring_digest is not None
        and adjudicated_anchoring_digest == selection_digest
    )
    # Only the concern class reaches a person, so only it needs the reviewer-facing
    # context; locator violations are remediated by the actor, which already has the text.
    unanchored_literal_excerpts = _literal_excerpts(
        target_text=target_text,
        tokens=unanchored_literals_in_selection or unanchored_literals_in_universe,
        observations=selected or task_state.observations,
    )
    insufficiency_declared_in_payload = _declares_insufficiency(target_text)

    # Deterministic deficit codes collection
    deficit_codes: list[str] = []
    if not selected_evidence_refs_valid:
        deficit_codes.append("invalid_evidence_ref")
    if not selected_evidence_refs_unique:
        deficit_codes.append("duplicate_evidence_ref")
    if not selected_observations_have_content:
        deficit_codes.append("empty_observation_content")
    if not provenance_resolvable:
        deficit_codes.append("unresolvable_provenance")
    if not known_steps:
        deficit_codes.append("unknown_step_lineage")
    if candidate_binding_required and not (candidate_snapshot_matches and candidate_selection_matches):
        deficit_codes.append("candidate_digest_mismatch")
    if factual_step_missing_evidence_burden:
        deficit_codes.append("missing_evidence_burden")
    if coverage_evaluated and not independent_sources_satisfied:
        deficit_codes.append("insufficient_independent_sources")
    if coverage_evaluated and not retrieved_content_satisfied:
        deficit_codes.append("retrieved_content_missing")
    if evidence_completion_attempt and selected_evidence_all_title_only:
        deficit_codes.append("title_only_evidence_fragility")
    if evidence_completion_attempt and circular_reference_detected:
        deficit_codes.append("circular_evidence_reference")
    if evidence_completion_attempt and unanchored_locators_in_selection:
        deficit_codes.append("unanchored_locators_in_selection")
    if evidence_completion_attempt and unanchored_locators_in_universe:
        deficit_codes.append("unanchored_locators_in_universe")
    if evidence_completion_attempt and unanchored_literals_in_selection:
        deficit_codes.append("unanchored_literals_in_selection")
    if evidence_completion_attempt and unanchored_literals_in_universe:
        deficit_codes.append("unanchored_literals_in_universe")

    current_evidence_episode_id = resolve_evidence_episode_id(task_state)
    stored_evidence_episode_id = getattr(
        task_state,
        "evidence_request_episode_id",
        None,
    )
    evidence_request_episode_count = (
        getattr(
            task_state,
            "evidence_request_episode_count",
            getattr(task_state, "evidence_request_count", 0),
        )
        if stored_evidence_episode_id == current_evidence_episode_id
        else 0
    )
    raw_facts = {
        "observation_count": len(task_state.observations),
        "observation_ids_unique": observation_ids_unique,
        "selected_evidence_ref_count": len(selected_refs),
        "selected_evidence_refs_valid": selected_evidence_refs_valid,
        "selected_evidence_refs_unique": selected_evidence_refs_unique,
        "selected_observations_have_content": selected_observations_have_content,
        "selected_observations_provenance_resolvable": provenance_resolvable,
        "selected_observations_belong_to_known_steps": known_steps,
        "selected_evidence_all_title_only": selected_evidence_all_title_only,
        "evidence_selection_digest": selection_digest,
        "evidence_snapshot_digest": snapshot_digest,
        "candidate_evidence_binding_required": candidate_binding_required,
        "candidate_evidence_binding_present": candidate_binding_present,
        "candidate_evidence_snapshot_matches": candidate_snapshot_matches,
        "candidate_evidence_selection_matches": candidate_selection_matches,
        "candidate_external_operation_digest_matches": candidate_operation_matches,
        "completion_attempt": completion_attempt,
        "factual_completion_attempt": factual_completion_attempt,
        "evidence_completion_attempt": evidence_completion_attempt,
        "evidence_request_count": getattr(task_state, "evidence_request_count", 0),
        "evidence_request_episode_count": evidence_request_episode_count,
        "evidence_request_episode_id": current_evidence_episode_id,
        # Famiglia 2 facts
        "produces_factual_claims": produces_factual_claims,
        "produces_governed_external_effect_proposal": produces_external_effect_proposal,
        "evidence_families_active": evidence_families_active,
        "evidence_burden_derived_from_task": (
            declared_evidence_burden is None and evidence_burden is not None
        ),
        "factual_step_missing_evidence_burden": factual_step_missing_evidence_burden,
        "coverage_evaluated": coverage_evaluated,
        "distinct_independent_sources_count": distinct_independent_sources_count,
        "retrieved_independent_sources_count": retrieved_independent_sources_count,
        "min_independent_sources_required": min_sources,
        "independent_sources_satisfied": independent_sources_satisfied,
        "retrieved_content_satisfied": retrieved_content_satisfied,
        "burden_satisfied": burden_satisfied,
        "insufficiency_declaration_permitted": bool(
            evidence_burden and evidence_burden.insufficiency_declaration_permitted
        ),
        "insufficiency_declared_in_payload": insufficiency_declared_in_payload,
        # Famiglia 4 facts
        "circular_reference_detected": circular_reference_detected,
        "circular_evidence_observation_ids": sorted(circular_evidence_reasons),
        # Famiglia 5 facts
        "literals_anchored_to_task": literals_anchored_to_task,
        "unanchored_locators_in_selection": unanchored_locators_in_selection,
        "unanchored_locators_in_universe": unanchored_locators_in_universe,
        "has_unanchored_locators_in_selection": len(unanchored_locators_in_selection) > 0,
        "has_unanchored_locators_in_universe": len(unanchored_locators_in_universe) > 0,
        "unanchored_literal_excerpts": unanchored_literal_excerpts,
        "unanchored_literals_in_selection": unanchored_literals_in_selection,
        "unanchored_literals_in_universe": unanchored_literals_in_universe,
        "has_unanchored_literals_in_selection": len(unanchored_literals_in_selection) > 0,
        "has_unanchored_literals_in_universe": len(unanchored_literals_in_universe) > 0,
        "anchoring_concern_adjudicated": anchoring_concern_adjudicated,
        # Aggregated deficit codes
        "deficit_codes": deficit_codes,
    }
    return {
        "raw_facts": raw_facts,
        "mode": "active",
        "decision_influence": True,
        "diagnostic_score": None,
        "diagnostic_level": "unscored",
        "score_components": {},
        "reason_facts": deficit_codes,
        "summary": "Concrete structural evidence facts collected across 5 families.",
        "score_formula_version": "evidence_structural_v2",
        "thresholds_version": thresholds_version,
    }


__all__ = ["build_evidence_signal", "extract_source_origin"]
