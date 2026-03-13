# auto_apply_app/interfaces/viewmodels/free_search_vm.py

from dataclasses import dataclass
from typing import List

@dataclass
class JobSnippetViewModel:
    """
    View Model for a single job in the free search results.
    Matches the exact JSON structure expected by the React component.
    """
    jobTitle: str
    companyName: str
    location: str
    descriptionSnippet: str
    jobBoard: str
    url: str

@dataclass
class FreeSearchResultViewModel:
    """
    View Model for the entire free search operation.
    Matches the JSON structure for the FreeSearchPage component.
    """
    jobs: List[JobSnippetViewModel]
    totalFound: int
    boardsSearched: List[str]
    status: str  # "success" or "error"
    errorMessage: str = ""