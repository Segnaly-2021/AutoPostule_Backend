# ==============================================================================
# FILE: auto_apply_app/infrastructures/api/cors.py
#
# WHAT IT DOES:
#   Holds the list of website origins (scheme + host + port) allowed to call this
#   API from a browser. The CORS middleware uses it, and the page-view tracker
#   uses it as a server-side origin check.
#
# USED BY:
#   - auto_apply_app/infrastructures/api/app.py: passes it to CORSMiddleware
#   - auto_apply_app/infrastructures/api/routers/analytics.py: drops page views from other origins
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================
"""
The single list of origins allowed to talk to this API.

Consumed by the CORS middleware and by the page-view tracker, which rejects requests
from anywhere else. Keeping one list means the browser-enforced check and the
server-enforced one cannot drift apart.
"""

ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "https://autopostule.com",
    "https://www.autopostule.com",
    "https://autopostule.netlify.app",
]
