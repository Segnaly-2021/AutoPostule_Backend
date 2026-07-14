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
