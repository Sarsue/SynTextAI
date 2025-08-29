"""
Repository manager that provides a unified interface to all repositories.

Acts as a facade over the specialized repositories to provide backward compatibility
with the original DocSynthStore interface while maintaining separation of concerns.
"""
from typing import Optional, List, Dict, Any, Tuple, AsyncGenerator, TypeVar, Type, Callable, Awaitable, Any, ContextManager
import logging
import asyncio
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession, AsyncEngine
from sqlalchemy.orm import sessionmaker

# Import domain models first to avoid circular imports
from .domain_models import Subscription, CardDetails
from .session_manager import SessionContextManager

logger = logging.getLogger(__name__)
T = TypeVar('T')

# Import repositories here to avoid circular imports
# These will be imported later when needed

class RepositoryManager:
    """
    Repository manager that coordinates access to all repositories.
    
    Provides a unified interface with proper session management using context managers
    to ensure resources are properly cleaned up.
    """
    
    def __init__(self, database_url: str, echo: bool = True):
        """
        Initialize the repository manager with a database URL.
        
        Args:
            database_url: Database connection URL as a string or URL-like object
            echo: Whether to enable SQL query logging
        """
        db_url_str = str(database_url) if hasattr(database_url, '__str__') else database_url
        self.engine: AsyncEngine = create_async_engine(
            db_url_str, 
            echo=echo,
            future=True,
            pool_pre_ping=True,
            pool_recycle=3600  # Recycle connections after 1 hour
        )
        
        self.async_session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False
        )
        
        # Initialize repository caches with None - they'll be created on first access
        self._user_repo = None
        self._chat_repo = None
        self._file_repo = None
        self._learning_material_repo = None
    
    def session_scope(self) -> SessionContextManager:
        """
        Get a session context manager.
        
        Example:
            async with repo_manager.session_scope() as session:
                # Use session here
                result = await session.execute(query)
                # Session is automatically committed if no exceptions occur
                # or rolled back if an exception is raised
        """
        return SessionContextManager(self.async_session_factory)
    
    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Async context manager for database sessions.
        
        This is maintained for backward compatibility. Prefer using session_scope()
        for new code as it provides better error handling.
        """
        async with self.session_scope() as session:
            yield session
    
    async def execute_in_session(self, operation: Callable[[AsyncSession], Awaitable[T]]) -> T:
        """
        Execute a function within a session with automatic commit/rollback.
        
        Args:
            operation: Async function that takes a session and returns a result
            
        Returns:
            The result of the operation
            
        Example:
            result = await repo_manager.execute_in_session(
                lambda s: s.execute(query).scalar_one()
            )
        """
        async with self.session_scope() as session:
            return await operation(session)
    
    @property
    def user_repo(self):
        """Lazily initialize and return the user repository."""
        if self._user_repo is None:
            from .async_user_repository import AsyncUserRepository
            self._user_repo = AsyncUserRepository(self)
        return self._user_repo
        
    @property
    def chat_repo(self):
        """Lazily initialize and return the chat repository."""
        if self._chat_repo is None:
            from .async_chat_repository import AsyncChatRepository
            self._chat_repo = AsyncChatRepository(self)
        return self._chat_repo
        
    @property
    def file_repo(self):
        """Lazily initialize and return the file repository."""
        if self._file_repo is None:
            from .async_file_repository import AsyncFileRepository
            self._file_repo = AsyncFileRepository(self)
        return self._file_repo
        
    @property
    def learning_material_repo(self):
        """Get the learning material repository."""
        if self._learning_material_repo is None:
            from .async_learning_material_repository import AsyncLearningMaterialRepository
            self._learning_material_repo = AsyncLearningMaterialRepository(self)
        return self._learning_material_repo
        
    async def add_flashcard(self, file_id: int, question: str, answer: str, key_concept_id: Optional[int] = None, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Add a new flashcard.
        
        Args:
            file_id: ID of the file this flashcard is associated with
            question: The flashcard question
            answer: The flashcard answer
            key_concept_id: Optional ID of the key concept this flashcard relates to
            user_id: ID of the user who owns this flashcard (required for access control)
            
        Returns:
            The created flashcard as a dictionary, or None if creation failed
        """
        if user_id is None:
            raise ValueError("user_id is required for access control")
            
        return await self.learning_material_repo.create_custom_flashcard(
            user_id=user_id,
            file_id=file_id,
            question=question,
            answer=answer,
            key_concept_id=key_concept_id
        )
        
    async def add_quiz_question(self, file_id: int, question: str, question_type: str, 
                              correct_answer: str, key_concept_id: Optional[int] = None, 
                              distractors: Optional[List[str]] = None, 
                              quiz_question_data: Optional[Dict[str, Any]] = None,
                              user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Add a new quiz question.
        
        Args:
            file_id: ID of the file this question is associated with
            question: The question text
            question_type: Type of question (e.g., 'MCQ', 'TF')
            correct_answer: The correct answer to the question
            key_concept_id: Optional ID of the key concept this question relates to
            distractors: List of incorrect answer options (for multiple choice)
            quiz_question_data: Additional question data as a dictionary
            user_id: ID of the user who owns this question (required for access control)
            
        Returns:
            The created quiz question as a dictionary, or None if creation failed
        """
        if user_id is None:
            raise ValueError("user_id is required for access control")
            
        # Verify the user has access to the file
        file_repo = self.file_repo
        has_access = await file_repo.check_user_file_ownership(file_id, user_id)
        if not has_access:
            logger.warning(f"User {user_id} does not have access to file {file_id}")
            return None
            
        return await self.learning_material_repo.create_quiz_question(
            file_id=file_id,
            question=question,
            question_type=question_type,
            correct_answer=correct_answer,
            key_concept_id=key_concept_id,
            distractors=distractors or [],
            quiz_question_data=quiz_question_data or {}
        )
    

    async def close(self):
        """Close the database engine and clean up resources."""
        if self.engine:
            await self.engine.dispose()

def get_repository_manager(database_url) -> RepositoryManager:
    """
    Create a new instance of RepositoryManager with the provided database URL.
    
    Args:
        database_url: Database connection URL (can be string, URL, or MultiHostUrl)
        
    Returns:
        RepositoryManager: A new repository manager instance
    """
    # Convert database_url to string if it has a __str__ method
    db_url = str(database_url) if hasattr(database_url, '__str__') else database_url
    return RepositoryManager(database_url=db_url)
