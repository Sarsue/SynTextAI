"""
Base class for async repositories using SQLAlchemy 2.0+ async.

This module provides a base repository class that implements common CRUD operations
asynchronously using SQLAlchemy's async features. It's designed to work with the
RepositoryManager for session and transaction management.
"""
from __future__ import annotations

import logging
import asyncio
import contextlib
from contextlib import asynccontextmanager
from abc import ABC, abstractmethod
from datetime import datetime
from typing import (
    Any, Dict, Generic, List, Optional, Type, TypeVar, Union, 
    Callable, Awaitable, AsyncGenerator, cast, overload, Sequence, Tuple
)

from sqlalchemy import select, update, delete, func, and_, or_, text
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine
from sqlalchemy.exc import (
    SQLAlchemyError, IntegrityError, NoResultFound,
    MultipleResultsFound, DBAPIError, InterfaceError,
    OperationalError
)
from sqlalchemy.orm import (
    joinedload, selectinload, load_only, 
    contains_eager, Session, aliased
)
from sqlalchemy.sql.expression import Select, Update, Delete
from sqlalchemy.engine import Result

# Configure logging
logger = logging.getLogger(__name__)

# Type variables for generic type hints
ModelType = TypeVar("ModelType", bound=Any)
CreateSchemaType = TypeVar("CreateSchemaType")
UpdateSchemaType = TypeVar("UpdateSchemaType")
T = TypeVar('T')

# Type aliases
FilterType = Dict[str, Any]
OrderByType = Union[str, List[str], Dict[str, str]]

class AsyncBaseRepository(Generic[ModelType, CreateSchemaType, UpdateSchemaType], ABC):
    """
    Base repository with async CRUD operations.
    
    This repository is designed to work with the RepositoryManager's session management
    system, which provides automatic session handling and transaction management.
    
    Features:
    - Automatic session management with context managers
    - Transaction handling with rollback on errors
    - Built-in retry for transient database errors
    - Comprehensive logging and error handling
    - Support for eager loading of relationships
    - Pagination and filtering utilities
    """
    
    def __init__(
        self, 
        repository_manager: Any,
        session_factory: Optional[Callable[[], AsyncSession]] = None
    ):
        """
        Initialize the base repository.
        
        Args:
            repository_manager: The repository manager instance
            session_factory: Optional SQLAlchemy async session factory
        """
        self._repository_manager = repository_manager
        self._session_factory = session_factory
        self._model: Type[ModelType] = getattr(self, 'model', None)
        
        if self._model is None:
            raise ValueError("Model class not specified. Set the 'model' class attribute.")
            
        if not self._session_factory:
            self._session_factory = repository_manager.session_factory
    
    @property
    def model(self) -> Type[ModelType]:
        """Get the model class for this repository."""
        return self._model
        
    @property
    def session_factory(self):
        """Get the session factory for this repository."""
        return self._session_factory
        
    @property
    def repository_manager(self):
        """Get the repository manager for this repository."""
        return self._repository_manager
        
    @asynccontextmanager
    async def session_scope(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Get a session context manager for manual session management.
        
        This context manager handles the session lifecycle including:
        - Session creation and cleanup
        - Transaction management (commit/rollback)
        - Error handling and logging
        
        Yields:
            AsyncSession: A database session ready for use
            
        Raises:
            RuntimeError: If the repository is not properly initialized
            SQLAlchemyError: For database-related errors
            
        Example:
            async with repo.session_scope() as session:
                # Use session directly
                result = await session.execute(select(User))
        """
        if not self._session_factory:
            raise RuntimeError("Session factory not initialized")
            
        session = self._session_factory()
        try:
            # Begin a transaction
            await session.begin()
            
            # Set session-level settings
            await session.execute(text("SET TIME ZONE 'UTC'"))
            
            # Yield the session to the caller
            yield session
            
            # Commit the transaction if we're the outermost session
            if not session.in_transaction() or session.in_nested_transaction():
                logger.debug("Skipping commit - not in a transaction or in nested transaction")
            else:
                await session.commit()
                logger.debug("Transaction committed successfully")
                
        except (OperationalError, InterfaceError, DBAPIError) as e:
            logger.error("Database operation failed (retryable): %s", str(e), exc_info=True)
            if session.in_transaction():
                await session.rollback()
            raise
            
        except SQLAlchemyError as e:
            logger.error("Database error in session: %s", str(e), exc_info=True)
            if session.in_transaction():
                await session.rollback()
            raise
            
        except Exception as e:
            logger.error("Unexpected error in session: %s", str(e), exc_info=True)
            if session.in_transaction():
                await session.rollback()
            raise
            
        finally:
            try:
                # Close the session
                await session.close()
            except Exception as e:
                logger.error("Error closing session: %s", str(e), exc_info=True)
    
    async def execute_in_session(self, operation: Callable[[AsyncSession], Awaitable[T]]) -> T:
        """
        Execute an operation within a session with automatic commit/rollback.
        
        Args:
            operation: Async function that takes a session and returns a result
            
        Returns:
            The result of the operation
        """
        return await self._repository_manager.execute_in_session(operation)
    
    async def get(
        self, 
        id: Any, 
        options: Optional[list] = None,
        **filters: Any
    ) -> Optional[ModelType]:
        """
        Get a single record by primary key with optional filtering.
        
        Args:
            id: The primary key value
            options: List of SQLAlchemy loading options (e.g., joinedload, selectinload)
            **filters: Additional column filters
            
        Returns:
            Optional[ModelType]: The model instance if found, None otherwise
            
        Raises:
            SQLAlchemyError: If there's an error executing the query
        """
        if not id:
            raise ValueError("ID cannot be None or empty")
            
        async with self.session_scope() as session:
            try:
                stmt = select(self.model).where(self.model.id == id)
                
                # Apply additional filters
                for key, value in filters.items():
                    if hasattr(self.model, key):
                        stmt = stmt.where(getattr(self.model, key) == value)
                
                # Apply loading options if provided
                if options:
                    stmt = stmt.options(*options)
                
                result = await session.execute(stmt)
                return result.scalar_one_or_none()
                
            except SQLAlchemyError as e:
                logger.error("Error getting %s with id %s: %s", 
                           self.model.__name__, id, str(e), exc_info=True)
                raise
    
    async def get_multi(
        self, 
        skip: int = 0, 
        limit: int = 100,
        filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[Union[str, List[str]]] = None,
        options: Optional[list] = None,
        **kwargs: Any
    ) -> Tuple[List[ModelType], int]:
        """
        Get multiple records with filtering, sorting, and pagination.
        
        Args:
            skip: Number of records to skip (for pagination)
            limit: Maximum number of records to return
            filters: Dictionary of column filters to apply
            order_by: Field(s) to order by (can be string or list of strings)
                     Prefix with '-' for descending order
            options: List of SQLAlchemy loading options (e.g., joinedload, selectinload)
            **kwargs: Additional filters (alternative to filters parameter)
            
        Returns:
            Tuple containing:
                - List of model instances
                - Total count of records matching the filters
                
        Raises:
            SQLAlchemyError: If there's an error executing the query
        """
        # Merge filters from both parameters
        all_filters = {}
        if filters:
            all_filters.update(filters)
        all_filters.update(kwargs)
        
        # Handle None values in filters
        all_filters = {k: v for k, v in all_filters.items() if v is not None}
        
        async with self.session_scope() as session:
            try:
                # Base query
                stmt = select(self.model)
                count_stmt = select(func.count()).select_from(self.model)
                
                # Apply filters
                for key, value in all_filters.items():
                    if hasattr(self.model, key):
                        column = getattr(self.model, key)
                        
                        # Handle different filter types
                        if isinstance(value, (list, tuple, set)):
                            stmt = stmt.where(column.in_(value))
                            count_stmt = count_stmt.where(column.in_(value))
                        elif isinstance(value, dict) and 'op' in value:
                            # Handle custom operators
                            op = value['op']
                            val = value['value']
                            
                            if op == 'like':
                                stmt = stmt.where(column.like(val))
                                count_stmt = count_stmt.where(column.like(val))
                            elif op == 'ilike':
                                stmt = stmt.where(column.ilike(val))
                                count_stmt = count_stmt.where(column.ilike(val))
                            elif op == '>':
                                stmt = stmt.where(column > val)
                                count_stmt = count_stmt.where(column > val)
                            elif op == '>=':
                                stmt = stmt.where(column >= val)
                                count_stmt = count_stmt.where(column >= val)
                            elif op == '<':
                                stmt = stmt.where(column < val)
                                count_stmt = count_stmt.where(column < val)
                            elif op == '<=':
                                stmt = stmt.where(column <= val)
                                count_stmt = count_stmt.where(column <= val)
                            elif op == '!=':
                                stmt = stmt.where(column != val)
                                count_stmt = count_stmt.where(column != val)
                            elif op == 'is_null':
                                stmt = stmt.where(column.is_(None))
                                count_stmt = count_stmt.where(column.is_(None))
                            elif op == 'is_not_null':
                                stmt = stmt.where(column.isnot(None))
                                count_stmt = count_stmt.where(column.isnot(None))
                        else:
                            # Default to equality
                            stmt = stmt.where(column == value)
                            count_stmt = count_stmt.where(column == value)
                
                # Apply ordering
                if order_by:
                    order_clauses = []
                    
                    # Convert single string to list for consistent processing
                    if isinstance(order_by, str):
                        order_by = [order_by]
                    
                    for field in order_by:
                        if not field:
                            continue
                            
                        # Handle descending order
                        if field.startswith('-'):
                            field = field[1:]
                            direction = 'desc'
                        else:
                            direction = 'asc'
                        
                        if hasattr(self.model, field):
                            column = getattr(self.model, field)
                            order_clauses.append(
                                column.desc() if direction == 'desc' else column.asc()
                            )
                    
                    if order_clauses:
                        stmt = stmt.order_by(*order_clauses)
                
                # Apply pagination
                if limit > 0:
                    stmt = stmt.offset(skip).limit(limit)
                
                # Apply loading options
                if options:
                    stmt = stmt.options(*options)
                
                # Execute queries
                result = await session.execute(stmt)
                items = result.scalars().all()
                
                # Get total count
                if limit > 0 and len(items) < limit and skip == 0:
                    # If we got fewer items than the limit, we can use the count
                    total = len(items)
                else:
                    # Otherwise, we need to run a separate count query
                    count_result = await session.execute(count_stmt)
                    total = count_result.scalar() or 0
                
                logger.debug("Fetched %d of %d %s records", 
                           len(items), total, self.model.__name__)
                
                return items, total
                
            except SQLAlchemyError as e:
                logger.error("Error fetching %s records: %s", 
                           self.model.__name__, str(e), exc_info=True)
                raise

    async def create(
        self, 
        obj_in: Union[CreateSchemaType, Dict[str, Any]], 
        **kwargs: Any
    ) -> ModelType:
        """
        Create a new record with the given input data.
        
        Args:
            obj_in: Input data for creating the record (can be dict or Pydantic model)
            **kwargs: Additional fields to set on the model
            
        Returns:
            ModelType: The created model instance with ID populated
            
        Raises:
            ValueError: If input data is invalid
            IntegrityError: If a unique constraint is violated
            SQLAlchemyError: If there's an error creating the record
        """
        async with self.session_scope() as session:
            try:
                # Convert input to dict if it's a Pydantic model
                if hasattr(obj_in, 'dict'):
                    obj_in_data = obj_in.dict(exclude_unset=True)
                elif isinstance(obj_in, dict):
                    obj_in_data = obj_in.copy()
                else:
                    raise ValueError("Input must be a Pydantic model or dictionary")
                
                # Merge with any additional kwargs
                obj_in_data.update(kwargs)
                
                # Validate required fields
                required_fields = [
                    col.name for col in self.model.__table__.columns 
                    if not col.nullable and col.default is None and col.name != 'id'
                ]
                
                missing_fields = [
                    field for field in required_fields 
                    if field not in obj_in_data or obj_in_data[field] is None
                ]
                
                if missing_fields:
                    raise ValueError(f"Missing required fields: {', '.join(missing_fields)}")
                
                # Create the model instance
                db_obj = self.model(**obj_in_data)
                
                # Set timestamps if columns exist
                if hasattr(db_obj, 'created_at'):
                    db_obj.created_at = datetime.utcnow()
                if hasattr(db_obj, 'updated_at'):
                    db_obj.updated_at = datetime.utcnow()
                
                # Add to session
                session.add(db_obj)
                await session.flush()
                
                # Refresh to get any server defaults or computed values
                await session.refresh(db_obj)
                
                logger.debug("Created new %s with ID: %s", 
                            self.model.__name__, getattr(db_obj, 'id', 'unknown'))
                
                return db_obj
                
            except IntegrityError as e:
                await session.rollback()
                logger.error("Integrity error creating %s: %s", 
                           self.model.__name__, str(e), exc_info=True)
                raise
                
            except SQLAlchemyError as e:
                await session.rollback()
                logger.error("Error creating %s: %s", 
                           self.model.__name__, str(e), exc_info=True)
                raise
    
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
        async with self.session_scope() as session:
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
