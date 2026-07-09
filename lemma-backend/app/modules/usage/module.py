"""Usage module registration."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from app.core.log.log import get_logger
from app.core.registry import LemmaModule

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = get_logger(__name__)


def _routers():
    from app.modules.usage.api.controllers import router as usage

    return [usage]


@asynccontextmanager
async def _pricing_coverage_lifespan(_app: "FastAPI") -> AsyncIterator[None]:
    """Warn at startup when a configured system:lemma model has no usage price.

    Log-only: the runtime fallback already prevents unpriced models from escaping
    metering, so this never blocks startup — it just surfaces misconfiguration
    (e.g. an env-overridden model list) so a real price can be added before the
    fallback kicks in.
    """
    try:
        from app.modules.agent.services.runtime_profile_service import (
            system_lemma_openai_catalog_model_names,
        )
        from app.modules.usage.services.usage_service import (
            assert_system_pricing_covers_catalog,
        )

        uncovered = assert_system_pricing_covers_catalog(
            system_lemma_openai_catalog_model_names()
        )
        if uncovered:
            logger.error(
                "system:lemma models missing usage pricing (runs will use "
                "fallback pricing): %s",
                ", ".join(uncovered),
            )
    except Exception:  # never block startup on the pricing coverage check
        logger.exception("Failed to run system model pricing coverage check")
    yield


module = LemmaModule(
    name="usage",
    routers=_routers,
    api_lifespans=(_pricing_coverage_lifespan,),
)
