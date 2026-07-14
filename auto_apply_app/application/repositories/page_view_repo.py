"""
This module defines the repository interface for PageView entity persistence.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from auto_apply_app.domain.entities.page_view import PageView


class PageViewRepository(ABC):
    """Repository interface for anonymous page-view tracking."""

    @abstractmethod
    async def save(self, page_view: PageView) -> None:
        """Append a single page view."""
        pass

    # "Visitors" is ambiguous, so we report three different numbers rather than pick one
    # and hope. The same person browsing 20 pages in a morning and again in the evening
    # is 1 unique visitor, 2 sessions, and 20 page views. All windows are half-open
    # [start, end) so a view at exactly midnight is counted once, in the day it belongs to.

    @abstractmethod
    async def count_unique_visitors_between(self, start: datetime, end: datetime) -> int:
        """Distinct visitor_id — i.e. distinct browsers/devices. The headline number."""
        pass

    @abstractmethod
    async def count_page_views_between(self, start: datetime, end: datetime) -> int:
        """Raw view count. Traffic volume, not people."""
        pass

    @abstractmethod
    async def count_signed_in_visitors_between(self, start: datetime, end: datetime) -> int:
        """
        Distinct visitors we could attribute to an account.

        The anonymous count is derived as (unique - signed_in) rather than queried, so the
        two always sum to the total — see GetAdminOverviewMetricsUseCase.
        """
        pass

    @abstractmethod
    async def count_sessions_between(
        self,
        start: datetime,
        end: datetime,
        inactivity_minutes: int = 30,
    ) -> int:
        """
        Count visits, where a visit ends after `inactivity_minutes` of silence.

        A session starts on a visitor's first view, or on any view that follows a gap
        longer than the threshold. 30 minutes is the industry default (GA, Plausible,
        Matomo), which keeps these numbers comparable to any other tool.

        Derived from the raw page_views at query time rather than from a client-supplied
        session id: the definition then lives in exactly one place, retuning the threshold
        is retroactive, and the browser has nothing extra to track.
        """
        pass

    @abstractmethod
    async def delete_older_than(self, cutoff: datetime) -> int:
        """
        Delete page views older than `cutoff`. Returns the number of rows removed.

        This table grows unbounded, so it needs a retention policy.
        """
        pass
