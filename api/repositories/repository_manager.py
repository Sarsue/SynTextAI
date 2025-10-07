from __future__ import annotations

import asyncio
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from .base_repository_manager import BaseRepositoryManager
from sqlalchemy import text
logger = logging.getLogger(__name__)

_repository_manager: Optional["RepositoryManager"] = None
_repository_lock = asyncio.Lock()


class RepositoryManager(BaseRepositoryManager):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._repos_initialized = False
        self._repo_lock = asyncio.Lock()

        self._user_repo = None
        self._file_repo = None
        self._chat_repo = None
        self._learning_material_repo = None

    async def initialize_repositories(self):
        if self._repos_initialized:
            return

        async with self._repo_lock:
            if self._repos_initialized:
                return

            from .async_user_repository import AsyncUserRepository
            from .async_file_repository import AsyncFileRepository
            from .async_chat_repository import AsyncChatRepository
            from .async_learning_material_repository import AsyncLearningMaterialRepository

            # Ensure DB connection works before initializing
            async with self.session_scope() as session:
                await session.execute(text("SELECT 1"))

            # Initialize repos with shared session factory
            self._user_repo = AsyncUserRepository(self, self.session_factory)
            self._file_repo = AsyncFileRepository(self, self.session_factory)
            self._chat_repo = AsyncChatRepository(self, self.session_factory)
            self._learning_material_repo = AsyncLearningMaterialRepository(self, self.session_factory)

            self._repos_initialized = True
            logger.info("✅ RepositoryManager: repositories initialized")

    async def ensure_initialized(self):
        if not self._repos_initialized:
            await self.initialize_repositories()

    async def get_user_repo(self):
        await self.ensure_initialized()
        return self._user_repo

    async def get_file_repo(self):
        await self.ensure_initialized()
        return self._file_repo

    async def get_chat_repo(self):
        await self.ensure_initialized()
        return self._chat_repo

    async def get_learning_material_repo(self):
        await self.ensure_initialized()
        return self._learning_material_repo

    async def close(self):
        logger.info("🧹 RepositoryManager: closing repositories and engine...")
        self._user_repo = self._file_repo = self._chat_repo = self._learning_material_repo = None
        self._repos_initialized = False
        await super().close()


async def get_repository_manager(
    *,
    database_url: Optional[str] = None,
    engine: Optional[AsyncEngine] = None,
    session_factory: Optional[async_sessionmaker] = None,
    engine_kwargs: Optional[dict] = None,
) -> RepositoryManager:
    """
    Global async-safe singleton to create or reuse the RepositoryManager.

    ✅ Prefer passing an `engine` and `session_factory` from db_utils
       so SSL and async setup are consistent across your app.
    """
    global _repository_manager

    if _repository_manager is None:
        async with _repository_lock:
            if _repository_manager is None:
                rm = RepositoryManager(
                    database_url=database_url,
                    engine=engine,
                    session_factory=session_factory,
                    engine_kwargs=engine_kwargs,
                )
                await rm.initialize_repositories()
                _repository_manager = rm
                logger.info("✅ RepositoryManager created")

    return _repository_manager
