"""
Base class for async repositories using SQLAlchemy 2.0 async.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, Generic, List, Optional, Type, TypeVar, Union, Callable, Awaitable
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update, delete, Select, Update, Delete, Result
import logging
from contextlib import asynccontextmanager

from .session_manager import SessionContextManager
from .repository_manager import RepositoryManager

logger = logging.getLogger(__name__)

ModelType = TypeVar("ModelType")
CreateSchemaType = TypeVar("CreateSchemaType")
UpdateSchemaType = TypeVar("UpdateSchemaType")
T = TypeVar('T')

class AsyncBaseRepository(Generic[ModelType, CreateSchemaType, UpdateSchemaType], ABC):
    """
    Base repository with async CRUD operations.
    
    This repository is designed to work with the RepositoryManager's session management
    system, which provides automatic session handling and transaction management.
    """
    
    def __init__(self, model: Type[ModelType], repository_manager: RepositoryManager):
        """
        Initialize with model and repository manager.
        
        Args:
            model: SQLAlchemy model class
            repository_manager: RepositoryManager instance for session management
        """
        self.model = model
        self._repository_manager = repository_manager
    
    @property
    def session_scope(self) -> SessionContextManager:
        """Get a session context manager for manual session management."""
        return self._repository_manager.session_scope()
    
    async def execute_in_session(self, operation: Callable[[AsyncSession], Awaitable[T]]) -> T:
        """
        Execute an operation within a session with automatic commit/rollback.
        
        Args:
            operation: Async function that takes a session and returns a result
            
        Returns:
            The result of the operation
        """
        return await self._repository_manager.execute_in_session(operation)
    
    async def get(self, id: Any) -> Optional[ModelType]:
        """
        Get a single record by ID.
        
        Args:
            id: The ID of the record to retrieve
            
        Returns:
            The model instance if found, None otherwise
        """
        async with self.session_scope as session:
            result = await session.execute(
                select(self.model).where(self.model.id == id)
            )
            return result.scalars().first()

    async def get_multi(
        self, *, skip: int = 0, limit: int = 100, **filters: Any
    ) -> List[ModelType]:
        """
        Get multiple records with optional filtering and pagination.
        
        Args:
            skip: Number of records to skip
            limit: Maximum number of records to return
            **filters: Filter criteria as keyword arguments
            
        Returns:
            List of model instances
        """
        async with self.session_scope as session:
            stmt = select(self.model).offset(skip).limit(limit)
            
            # Add filters if provided
            for key, value in filters.items():
                if hasattr(self.model, key):
                    stmt = stmt.where(getattr(self.model, key) == value)
            
            result = await session.execute(stmt)
            return result.scalars().all()

    async def create(self, *, obj_in: Union[CreateSchemaType, Dict[str, Any]]) -> ModelType:
        """
        Create a new record.
        
        Args:
            obj_in: Pydantic model or dictionary containing the data
            
        Returns:
            The created model instance
        """
        async with self.session_scope as session:
            # Handle both Pydantic models and dictionaries
            if hasattr(obj_in, 'dict'):
                obj_data = obj_in.dict()
            else:
                obj_data = dict(obj_in)
                
            db_obj = self.model(**obj_data)
            session.add(db_obj)
            await session.flush()  # Flush to get the ID if needed
            await session.refresh(db_obj)
            return db_obj
            # Session will be committed when the context exits
    
    async def update(
        self,
        *, 
        db_obj: ModelType, 
        obj_in: Union[UpdateSchemaType, Dict[str, Any]]
    ) -> ModelType:
        """
        Update a record.
        
        Args:
            db_obj: The database object to update
            obj_in: Pydantic model or dictionary containing the update data
            
        Returns:
            The updated model instance
        """
        async with self.session_scope as session:
            # Handle both Pydantic models and dictionaries
            if hasattr(obj_in, 'dict'):
                obj_data = obj_in.dict(exclude_unset=True)
            else:
                obj_data = {k: v for k, v in obj_in.items() if v is not None}
            
            # Update the object attributes
            for field in obj_data:
                if hasattr(db_obj, field):
                    setattr(db_obj, field, obj_data[field])
            
            session.add(db_obj)
            await session.flush()
            await session.refresh(db_obj)
            return db_obj
            # Session will be committed when the context exits

    async def delete(self, *, id: Any) -> bool:
        """
        Delete a record by ID.
        
        Args:
            id: The ID of the record to delete
            
        Returns:
            bool: True if a record was deleted, False otherwise
        """
        async with self.session_scope as session:
            result = await session.execute(
                delete(self.model).where(self.model.id == id)
            )
            # Session will be committed when the context exits
            return result.rowcount > 0
    
    async def delete_multi(self, **filters: Any) -> int:
        """
        Delete multiple records matching the given filters.
        
        Args:
            **filters: Filter criteria as keyword arguments
            
        Returns:
            int: Number of records deleted
        """
        async with self.session_scope as session:
            stmt = delete(self.model)
            
            # Add filters if provided
            for key, value in filters.items():
                if hasattr(self.model, key):
                    stmt = stmt.where(getattr(self.model, key) == value)
            
            result = await session.execute(stmt)
            return result.rowcount
    
    async def exists(self, **filters: Any) -> bool:
        """
        Check if any record matches the given filters.
        
        Args:
            **filters: Filter criteria as keyword arguments
            
        Returns:
            bool: True if at least one matching record exists
        """
        async with self.session_scope as session:
            stmt = select(self.model).limit(1)
            
            # Add filters if provided
            for key, value in filters.items():
                if hasattr(self.model, key):
                    stmt = stmt.where(getattr(self.model, key) == value)
            
            result = await session.execute(stmt)
            return result.scalars().first() is not None
    
    async def count(self, **filters: Any) -> int:
        """
        Count records matching the given filters.
        
        Args:
            **filters: Filter criteria as keyword arguments
            
        Returns:
            int: Number of matching records
        """
        from sqlalchemy import func
        
        async with self.session_scope as session:
            stmt = select(func.count()).select_from(self.model)
            
            # Add filters if provided
            for key, value in filters.items():
                if hasattr(self.model, key):
                    stmt = stmt.where(getattr(self.model, key) == value)
            
            result = await session.execute(stmt)
            return result.scalar_one()
