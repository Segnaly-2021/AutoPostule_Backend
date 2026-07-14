# auto_apply_app/infrastructures/api/routers/analytics.py
#
# Visitor tracking. This is the only unauthenticated write endpoint in the app — it has
# to be, since the whole point is counting people who have not logged in — so it is
# deliberately narrow:
#
#   * It stores no PII. No IP, no user agent, no cookie. The IP is used only in memory,
#     by the rate limiter's key function; the user agent only for the bot check below.
#   * It always answers 204 with an empty body. Bot traffic and disallowed origins are
#     dropped silently, so a caller can never tell whether its write landed, and there is
#     no response content to probe.
#   * Failures are swallowed. Tracking must never break a page load for a real visitor.
#
# On the URL: this is mounted at /api/v1/site/ping, NOT at anything containing
# "analytics", "track" or "page-view". Ad-blockers and privacy filter lists match on those
# substrings even for first-party requests, and a blocked request fails silently — we
# would undercount every visitor running uBlock and never know by how much. The module
# keeps its honest name; only the public URL is neutral.

import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from auto_apply_app.infrastructures.api.cors import ALLOWED_ORIGINS
from auto_apply_app.infrastructures.api.dependencies.auth_deps import OptionalUserId
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.api.rate_limit import limiter
from auto_apply_app.infrastructures.api.schema.analytics_schema import PageViewCreateSchema
from auto_apply_app.infrastructures.configuration.container import Application
from auto_apply_app.interfaces.controllers.analytics_controllers import AnalyticsController

logger = logging.getLogger(__name__)

router = APIRouter()

_BOT_USER_AGENT = re.compile(
    r"bot|crawl|spider|slurp|headless|curl|wget|python-requests|httpx|scrapy",
    re.IGNORECASE,
)


# Dependency Boilerplate
def get_analytics_controller(
    app_container: Annotated[Application, Depends(get_container)]
) -> AnalyticsController:
    return app_container.analytics_controller


AnalyticsControllerDep = Annotated[AnalyticsController, Depends(get_analytics_controller)]


@router.post(
    "/ping",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Record a page view",
)
@limiter.limit("60/minute")
async def record_page_view(
    request: Request,
    data: PageViewCreateSchema,
    controller: AnalyticsControllerDep,
    user_id: OptionalUserId,
):
    user_agent = request.headers.get("user-agent", "")
    origin = request.headers.get("origin", "")

    # Drop bots and anything not coming from one of our own front-ends. Silently: an
    # identical 204 either way means a scraper learns nothing from probing this.
    if not user_agent or _BOT_USER_AGENT.search(user_agent) or origin not in ALLOWED_ORIGINS:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # user_id is whatever the caller's token happened to say, or None. It is never
    # required: an anonymous visitor is the normal case, not an error. Identity comes
    # from the token, never from the request body — the body stays PII-free.
    result = await controller.handle_record_page_view(
        visitor_id=data.visitor_id,
        path=data.path,
        referrer=data.referrer,
        user_id=user_id,
    )

    if not result.is_success:
        # Log it, but never surface it: a visitor's page load must not fail because our
        # analytics did.
        logger.warning("page_view_record_failed reason=%s", result.error.message)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
