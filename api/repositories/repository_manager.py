"""
Repository manager that provides access to all async repositories.

This is a simple container that holds references to all async repositories,
providing a unified interface for the application to access database operations.
"""
from typing import Optional
import logging

from .async_user_repository import AsyncUserRepository
from .async_chat_repository import AsyncChatRepository
from .async_file_repository import AsyncFileRepository
from .async_learning_material_repository import AsyncLearningMaterialRepository

logger = logging.getLogger(__name__)


class RepositoryManager:
    """
    Repository manager that coordinates access to all async repositories.

    Provides a unified interface for accessing async database repositories.
    Routes call repository methods directly (e.g., store.user_repo.get_user_id_from_email()).
    """

    def __init__(self, database_url: str = None):
        """
        Initialize the repository manager with all async repositories.

        Args:
            database_url: The database connection URL. If None, uses centralized async URL.
        """
        if database_url is None:
            from ..models.async_db import get_database_url
            database_url = get_database_url()

        logger.info("Initializing async repositories...")
        self.user_repo = AsyncUserRepository(database_url)
        self.chat_repo = AsyncChatRepository(database_url)
        self.file_repo = AsyncFileRepository(database_url)
        self.learning_material_repo = AsyncLearningMaterialRepository(database_url)
        logger.info("All async repositories initialized successfully")
