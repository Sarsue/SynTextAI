# repository_manager.py
from __future__ import annotations
import asyncio, logging
from typing import Optional
from .base_repository_manager import BaseRepositoryManager
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

logger = logging.getLogger(__name__)
_repository_manager: Optional["RepositoryManager"]=None
_repository_lock = asyncio.Lock()

class RepositoryManager(BaseRepositoryManager):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._repos_initialized=False
        self._repo_lock=asyncio.Lock()
        self._user_repo=None
        self._file_repo=None
        self._chat_repo=None
        self._learning_material_repo=None
        self._key_concept_repo=None

    async def initialize_repositories(self):
        if self._repos_initialized: return
        async with self._repo_lock:
            if self._repos_initialized: return
            from .async_user_repository import AsyncUserRepository
            from .async_file_repository import AsyncFileRepository
            from .async_chat_repository import AsyncChatRepository
            from .async_learning_material_repository import AsyncLearningMaterialRepository
            from .async_key_concept_repository import AsyncKeyConceptRepository
            async with self.session_scope() as session: pass
            self._user_repo = AsyncUserRepository(self,self.session_factory)
            self._file_repo = AsyncFileRepository(self,self.session_factory)
            self._chat_repo = AsyncChatRepository(self,self.session_factory)
            self._learning_material_repo = AsyncLearningMaterialRepository(self,self.session_factory)
            self._key_concept_repo = AsyncKeyConceptRepository(self,self.session_factory)
            self._repos_initialized=True
            logger.info("RepositoryManager: repositories initialized")

    async def ensure_initialized(self):
        if not self._repos_initialized: await self.initialize_repositories()

    async def get_user_repo(self): await self.ensure_initialized(); return self._user_repo
    async def get_file_repo(self): await self.ensure_initialized(); return self._file_repo
    async def get_chat_repo(self): await self.ensure_initialized(); return self._chat_repo
    async def get_learning_material_repo(self): await self.ensure_initialized(); return self._learning_material_repo
    async def get_key_concept_repo(self): await self.ensure_initialized(); return self._key_concept_repo

    async def close(self):
        self._user_repo=self._file_repo=self._chat_repo=self._learning_material_repo=self._key_concept_repo=None
        self._repos_initialized=False
        await super().close()

async def get_repository_manager(
    *, database_url: Optional[str]=None, engine: Optional[AsyncEngine]=None,
    session_factory: Optional[async_sessionmaker]=None, engine_kwargs: Optional[dict]=None
) -> RepositoryManager:
    global _repository_manager
    if _repository_manager is None:
        async with _repository_lock:
            if _repository_manager is None:
                rm=RepositoryManager(database_url=database_url,engine=engine,session_factory=session_factory,engine_kwargs=engine_kwargs)
                await rm.initialize_repositories()
                _repository_manager=rm
    return _repository_manager
