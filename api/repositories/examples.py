"""
Examples demonstrating how to use the Unit of Work pattern with repositories.
This file is for reference only - it is not meant to be imported or executed.
"""

# Example 1: Refactoring an existing repository method to use UnitOfWork
# ---------------------------------------------------------------------

# BEFORE:
def add_key_concept_old(self, file_id: int, concept_title: str, concept_explanation: str):
    session = self.get_session()
    try:
        new_concept = KeyConceptORM(
            file_id=file_id,
            concept=concept_title,
            explanation=concept_explanation
        )
        session.add(new_concept)
        session.commit()
        return new_concept.id
    except Exception as e:
        session.rollback()
        logger.error(f"Error adding key concept: {e}", exc_info=True)
        return None
    finally:
        session.close()

# AFTER - Option 1: Using UnitOfWork class
def add_key_concept_uow(self, file_id: int, concept_title: str, concept_explanation: str):
    with self.get_unit_of_work() as uow:
        try:
            new_concept = KeyConceptORM(
                file_id=file_id,
                concept=concept_title, 
                explanation=concept_explanation
            )
            uow.session.add(new_concept)
            # No need for commit/rollback/close - handled by UnitOfWork
            return new_concept.id
        except Exception as e:
            # Exception handling is still possible for specific cases
            logger.error(f"Error adding key concept: {e}", exc_info=True)
            return None

# AFTER - Option 2: Using transactional session context manager
def add_key_concept_transactional(self, file_id: int, concept_title: str, concept_explanation: str):
    with self.transactional_session() as session:
        # If an exception occurs, transaction will be rolled back automatically
        new_concept = KeyConceptORM(
            file_id=file_id,
            concept=concept_title, 
            explanation=concept_explanation
        )
        session.add(new_concept)
        return new_concept.id

# AFTER - Option 3: Using execute_transactional helper
def add_key_concept_execute(self, file_id: int, concept_title: str, concept_explanation: str):
    def _operation(session):
        new_concept = KeyConceptORM(
            file_id=file_id,
            concept=concept_title,
            explanation=concept_explanation
        )
        session.add(new_concept)
        return new_concept.id
        
    return self.execute_transactional(_operation, "add_key_concept")


# Example 2: Complex transaction with multiple operations
# ------------------------------------------------------

def transfer_content_between_files(self, source_file_id: int, target_file_id: int, concept_ids: list[int]):
    """Transfer key concepts between files within a single transaction."""
    
    with self.get_unit_of_work() as uow:
        try:
            # First check both files exist
            source_file = uow.session.query(FileORM).filter_by(id=source_file_id).first()
            target_file = uow.session.query(FileORM).filter_by(id=target_file_id).first()
            
            if not source_file or not target_file:
                return False
                
            # Get all concepts to transfer
            concepts = uow.session.query(KeyConceptORM).filter(
                KeyConceptORM.file_id == source_file_id,
                KeyConceptORM.id.in_(concept_ids)
            ).all()
            
            # Update each concept's file_id
            for concept in concepts:
                concept.file_id = target_file_id
                
            # Update count metadata on both files
            source_file.concept_count = uow.session.query(KeyConceptORM).filter_by(
                file_id=source_file_id
            ).count()
            
            target_file.concept_count = uow.session.query(KeyConceptORM).filter_by(
                file_id=target_file_id
            ).count()
            
            # All operations happen in the same transaction
            # UnitOfWork will handle commit/rollback automatically
            return True
        except Exception as e:
            logger.error(f"Error transferring concepts between files: {e}", exc_info=True)
            return False


# Example 3: Handling multiple repositories in a single transaction
# ----------------------------------------------------------------

class TransactionManager:
    """
    Coordinate transactions across multiple repositories.
    This is useful when a single operation spans multiple repositories.
    """
    
    def __init__(self, database_url=None):
        """Set up shared session factory for all repositories."""
        if database_url is None:
            import os
            database_url = os.environ.get('DATABASE_URL')
            if not database_url:
                raise ValueError("No database URL provided and DATABASE_URL environment variable not set")
                
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        
        self.engine = create_engine(
            database_url,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        self.Session = sessionmaker(bind=self.engine)
        self.unit_of_work = UnitOfWork(self.Session)
        
    def __enter__(self):
        return self.unit_of_work.__enter__()
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        return self.unit_of_work.__exit__(exc_type, exc_val, exc_tb)


# Usage example for transaction manager
def create_learning_materials(learning_repo, file_repo, user_repo, file_id, user_id):
    """
    Create learning materials across multiple repositories in a single transaction.
    """
    # Create a transaction manager
    tx_manager = TransactionManager()
    
    # Use it to coordinate across multiple repositories
    with tx_manager as uow:
        # All operations use the same session
        file = file_repo.get_file_by_id_with_session(uow.session, file_id)
        user = user_repo.get_user_by_id_with_session(uow.session, user_id)
        
        # Create key concept
        new_concept = KeyConceptORM(
            file_id=file_id,
            concept="Example concept",
            explanation="This is an example",
            created_by=user.id
        )
        uow.session.add(new_concept)
        
        # Update file metadata
        file.has_learning_materials = True
        file.last_updated = datetime.now()
        
        # Update user activity
        user.last_activity = datetime.now()
        
    # Transaction is automatically committed if no exceptions occur
    # or rolled back if any exception occurs
