"""FastAPI route handlers."""

from pitwall.api.routes.burn_rate import burn_rate_router
from pitwall.api.routes.cost import router as cost_router
from pitwall.api.routes.gateway_models import router as gateway_models_router
from pitwall.api.routes.guardrails import router as guardrail_router
from pitwall.api.routes.inference import router as inference_router
from pitwall.api.routes.jobs import router as jobs_router
from pitwall.api.routes.leases import router as lease_router
from pitwall.api.routes.models import model_catalogue_router
from pitwall.api.routes.onboarding import router as onboarding_router
from pitwall.api.routes.openai import router as openai_router
from pitwall.api.routes.provider_operations import provider_operations_router
from pitwall.api.routes.quotas import router as quotas_router
from pitwall.api.routes.routing import routing_router
from pitwall.api.routes.runpod_market import router as runpod_market_router
from pitwall.api.routes.runpod_resources import router as runpod_resources_router
from pitwall.api.routes.serve import router as serve_router
from pitwall.api.routes.volume_files import volume_file_router
from pitwall.api.routes.webhook_subscriptions import router as webhook_subscription_router

__all__ = [
    "burn_rate_router",
    "cost_router",
    "gateway_models_router",
    "guardrail_router",
    "inference_router",
    "jobs_router",
    "lease_router",
    "model_catalogue_router",
    "onboarding_router",
    "openai_router",
    "provider_operations_router",
    "quotas_router",
    "routing_router",
    "runpod_market_router",
    "runpod_resources_router",
    "serve_router",
    "volume_file_router",
    "webhook_subscription_router",
]
