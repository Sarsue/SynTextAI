"""
Repositories package for the DocSynth application.

This package contains modular repositories that implement the repository pattern,
separating domain models from database operations following the Single Responsibility Principle.
"""
from .repository_manager import RepositoryManager, get_repository_manager

__all__ = ['RepositoryManager', 'get_repository_manager']
