import logging
from typing import Optional

from reclaim.config import Settings, settings, sanitize_secret
from reclaim.llm.base import LLMProvider
from reclaim.llm.gemini import GeminiProvider
from reclaim.llm.groq import GroqProvider
from reclaim.llm.mock import MockProvider
from reclaim.llm.exceptions import (
    ProviderAuthError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderUnavailableError,
    ProviderConfigurationError,
    LLMError
)

logger = logging.getLogger(__name__)


async def get_llm_provider(custom_settings: Optional[Settings] = None) -> LLMProvider:
    """
    Factory function resolving the active LLM provider based on configuration.

    Modes:
    - 'mock': Returns MockProvider (offline, zero quota, zero network).
    - 'gemini': Explicit Gemini only. Never silently falls back to Groq.
    - 'groq': Explicit Groq only. Never silently falls back to Gemini.
    - 'auto': Tests Gemini credentials & probe; falls back to Groq if Gemini is
              unusable due to quota/rate-limit/auth/availability; fails clearly if both fail.
    """
    cfg = custom_settings or settings
    mode = cfg.llm_provider.lower()

    if mode == "mock":
        logger.info("[LLM FACTORY] Selected MOCK provider (offline development / deterministic mode).")
        return MockProvider()

    gemini_key = cfg.get_effective_gemini_key()
    groq_key = cfg.get_effective_groq_key()

    if mode == "gemini":
        if not gemini_key:
            raise ProviderConfigurationError(
                "LLM_PROVIDER is explicitly set to 'gemini', but GEMINI_API_KEY (or GOOGLE_API_KEY) is missing. "
                "Set GEMINI_API_KEY in your .env file or environment.",
                provider="gemini"
            )
        logger.info(f"[LLM FACTORY] Explicitly configuring GEMINI provider (Key: {sanitize_secret(gemini_key)}, Model: {cfg.gemini_model})")
        return GeminiProvider(api_key=gemini_key, model_name=cfg.gemini_model, timeout_seconds=cfg.llm_timeout_seconds)

    if mode == "groq":
        if not groq_key:
            raise ProviderConfigurationError(
                "LLM_PROVIDER is explicitly set to 'groq', but GROQ_API_KEY is missing. "
                "Set GROQ_API_KEY in your .env file or environment.",
                provider="groq"
            )
        logger.info(f"[LLM FACTORY] Explicitly configuring GROQ provider (Key: {sanitize_secret(groq_key)}, Model: {cfg.groq_model})")
        return GroqProvider(api_key=groq_key, model_name=cfg.groq_model, timeout_seconds=cfg.llm_timeout_seconds)

    if mode == "auto":
        logger.info("[LLM FACTORY] AUTO provider selection initiating...")

        # Step 1: Check Gemini credentials
        gemini_candidate: Optional[GeminiProvider] = None
        if gemini_key:
            try:
                logger.info(f"[LLM FACTORY] Probing Gemini availability with model {cfg.gemini_model}...")
                candidate = GeminiProvider(
                    api_key=gemini_key,
                    model_name=cfg.gemini_model,
                    timeout_seconds=cfg.llm_timeout_seconds
                )
                await candidate.test_probe()
                logger.info("[LLM FACTORY] Gemini probe SUCCESSFUL. Selected GEMINI as primary provider.")
                return candidate
            except (ProviderQuotaError, ProviderRateLimitError, ProviderUnavailableError, ProviderAuthError) as err:
                logger.warning(
                    f"[LLM FACTORY] Gemini failed availability probe ({type(err).__name__}: {err.message}). "
                    "Attempting automatic fallback to Groq..."
                )
            except Exception as unk_err:
                logger.warning(
                    f"[LLM FACTORY] Gemini encountered unexpected probe failure: {unk_err}. "
                    "Attempting fallback to Groq..."
                )
        else:
            logger.info("[LLM FACTORY] GEMINI_API_KEY not configured. Checking Groq...")

        # Step 2: Fallback to Groq
        if groq_key:
            for candidate_model in [cfg.groq_model, cfg.groq_fallback_model]:
                try:
                    logger.info(f"[LLM FACTORY] Probing Groq availability with model {candidate_model}...")
                    groq_candidate = GroqProvider(
                        api_key=groq_key,
                        model_name=candidate_model,
                        timeout_seconds=cfg.llm_timeout_seconds
                    )
                    await groq_candidate.test_probe()
                    logger.info(f"[LLM FACTORY] Groq probe SUCCESSFUL. Selected GROQ ({candidate_model}) as active provider.")
                    return groq_candidate
                except (ProviderQuotaError, ProviderRateLimitError, ProviderUnavailableError, ProviderAuthError) as groq_err:
                    logger.warning(
                        f"[LLM FACTORY] Groq probe failed for {candidate_model} ({type(groq_err).__name__}: {groq_err.message})."
                    )
                except Exception as unk_groq_err:
                    logger.warning(f"[LLM FACTORY] Groq encountered probe error on {candidate_model}: {unk_groq_err}")
        else:
            logger.info("[LLM FACTORY] GROQ_API_KEY not configured.")

        # Step 3: Clear Failure Message
        raise ProviderConfigurationError(
            "Neither Gemini nor Groq is currently usable in AUTO mode.\n"
            "Please check your .env configuration:\n"
            "  1. Set GEMINI_API_KEY (from https://aistudio.google.com/)\n"
            "  2. Or set GROQ_API_KEY (from https://console.groq.com/keys)\n"
            "  3. Or run with LLM_PROVIDER=mock for offline development/testing.",
            provider="auto"
        )

    raise ProviderConfigurationError(
        f"Unsupported LLM_PROVIDER mode: '{mode}'. Must be one of: 'auto', 'gemini', 'groq', 'mock'.",
        provider=mode
    )
