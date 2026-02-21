from __future__ import annotations

from awe_agentcheck.participants import Participant
from awe_agentcheck.task_options import (
    normalize_participant_agent_overrides as normalize_participant_agent_overrides_shared,
    normalize_participant_model_params as normalize_participant_model_params_shared,
    normalize_participant_models as normalize_participant_models_shared,
    normalize_provider_model_params as normalize_provider_model_params_shared,
    normalize_provider_models as normalize_provider_models_shared,
    normalize_repair_mode as normalize_repair_mode_shared,
    resolve_agent_toggle_for_participant as resolve_agent_toggle_for_participant_shared,
    resolve_model_for_participant as resolve_model_for_participant_shared,
    resolve_model_params_for_participant as resolve_model_params_for_participant_shared,
)


def normalize_provider_models(value: dict[str, str] | None) -> dict[str, str]:
    return normalize_provider_models_shared(value, strict=False)


def normalize_provider_model_params(value: dict[str, str] | None) -> dict[str, str]:
    return normalize_provider_model_params_shared(value, strict=False)


def normalize_participant_models(value: dict[str, str] | None) -> dict[str, str]:
    return normalize_participant_models_shared(
        value,
        known_participants=None,
        strict=False,
        include_lower_alias=True,
    )


def normalize_participant_model_params(value: dict[str, str] | None) -> dict[str, str]:
    return normalize_participant_model_params_shared(
        value,
        known_participants=None,
        strict=False,
        include_lower_alias=True,
    )


def normalize_participant_agent_overrides(value: dict[str, bool] | None) -> dict[str, bool]:
    return normalize_participant_agent_overrides_shared(
        value,
        known_participants=None,
        required_provider=None,
        field='participant_agent_overrides',
        strict=False,
        include_lower_alias=True,
    )


def resolve_agent_toggle_for_participant(
    *,
    participant: Participant,
    global_enabled: bool,
    overrides: dict[str, bool],
) -> bool:
    return resolve_agent_toggle_for_participant_shared(
        participant_id=participant.participant_id,
        global_enabled=global_enabled,
        overrides=overrides,
    )


def resolve_model_for_participant(
    *,
    participant: Participant,
    provider_models: dict[str, str],
    participant_models: dict[str, str],
) -> str | None:
    return resolve_model_for_participant_shared(
        participant_id=participant.participant_id,
        provider=participant.provider,
        provider_models=provider_models,
        participant_models=participant_models,
    )


def resolve_model_params_for_participant(
    *,
    participant: Participant,
    provider_model_params: dict[str, str],
    participant_model_params: dict[str, str],
) -> str | None:
    return resolve_model_params_for_participant_shared(
        participant_id=participant.participant_id,
        provider=participant.provider,
        provider_model_params=provider_model_params,
        participant_model_params=participant_model_params,
    )


def normalize_repair_mode(value: str | None) -> str:
    return normalize_repair_mode_shared(value, strict=False)
