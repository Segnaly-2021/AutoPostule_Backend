# auto_apply_app/infrastructures/api/routers/messaging.py
#
# Unsubscribe. Two endpoints on one path, because a mail client can arrive by
# either verb:
#
#   GET   — a person clicking the footer link. Answers HTML, not JSON: the
#           response is read by a browser, and a raw {"unsubscribed": true} would
#           look like a failure to the one audience that ever sees it.
#   POST  — RFC 8058 one-click, triggered by the List-Unsubscribe-Post header
#           when Gmail or Outlook renders its own "unsubscribe" button. The user
#           never leaves their inbox, so this answers 204 with no body.
#
# Both are unauthenticated by design. The token carries the subject, so nobody can
# opt out an address they do not hold a link for, and requiring a login would make
# the link useless to exactly the people most likely to click it.
#
# An invalid or expired token still answers 200 with a neutral page rather than
# 401: this endpoint is reachable by anyone, and a distinguishable failure would
# turn it into an oracle for whether a token is live.

import html
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import HTMLResponse

from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.api.rate_limit import limiter
from auto_apply_app.infrastructures.configuration.container import Application
from auto_apply_app.interfaces.controllers.auth_controllers import AuthController

logger = logging.getLogger(__name__)

router = APIRouter()


def get_auth_controller(
    app_container: Annotated[Application, Depends(get_container)]
) -> AuthController:
    return app_container.auth_controller


AuthControllerDep = Annotated[AuthController, Depends(get_auth_controller)]


def _page(heading: str, body: str) -> str:
    """A self-contained confirmation page.

    Inline styles and no assets on purpose: this is served by the API, which hosts
    no static files, and it has to render the same in a webmail preview pane as in
    a browser tab.
    """
    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex">
  <title>AutoPostule</title>
</head>
<body style="margin:0;padding:48px 16px;background:#f6f7f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2430;">
  <div style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:12px;padding:40px 32px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
    <h1 style="margin:0 0 12px;font-size:20px;font-weight:600;">{html.escape(heading)}</h1>
    <p style="margin:0;font-size:15px;line-height:1.6;color:#5b6270;">{html.escape(body)}</p>
  </div>
</body>
</html>"""


_CONFIRMED = _page(
    "Vous êtes désabonné",
    "Vous ne recevrez plus nos messages d'information. "
    "Les emails liés à votre compte et à vos candidatures continueront de vous être envoyés.",
)

# Shown for a bad or expired token. Worded so it is not an admission that the
# token was rejected -- see the oracle note above.
_NEUTRAL = _page(
    "Lien de désabonnement",
    "Ce lien n'est plus valide. Vous pouvez gérer vos préférences depuis votre compte AutoPostule.",
)


@router.get(
    "/unsubscribe",
    response_class=HTMLResponse,
    include_in_schema=False,
    summary="Opt out of marketing email (browser click)",
)
@limiter.limit("30/minute")
async def unsubscribe_page(
    request: Request,
    controller: AuthControllerDep,
    token: Annotated[str, Query(max_length=2048)],
) -> HTMLResponse:
    result = await controller.handle_unsubscribe(token)
    if result.is_success:
        return HTMLResponse(_CONFIRMED)

    logger.info("Unsubscribe link rejected: %s", result.error.reason)
    return HTMLResponse(_NEUTRAL)


@router.post(
    "/unsubscribe",
    status_code=status.HTTP_204_NO_CONTENT,
    include_in_schema=False,
    summary="Opt out of marketing email (RFC 8058 one-click)",
)
@limiter.limit("30/minute")
async def unsubscribe_one_click(
    request: Request,
    controller: AuthControllerDep,
    token: Annotated[str, Query(max_length=2048)],
) -> Response:
    # Always 204, even on a bad token. The mail provider making this call shows the
    # user an error if it gets one, and there is nothing they could do about it.
    result = await controller.handle_unsubscribe(token)
    if not result.is_success:
        logger.info("One-click unsubscribe rejected: %s", result.error.reason)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
