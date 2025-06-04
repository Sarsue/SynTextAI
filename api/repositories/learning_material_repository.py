"""
Repository for managing learning materials like key concepts, flashcards, and quizzes.
"""
from typing import Optional, List, Dict, Any
import logging
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .base_repository import BaseRepository
from .domain_models import KeyConcept

# Import ORM models from the new models module
from models import KeyConcept as KeyConceptORM

logger = logging.getLogger(__name__)


class LearningMaterialRepository(BaseRepository):
    """Repository for learning material operations."""
    
    def add_key_concept(
        self,
        file_id: int,
        concept_title: str,
        concept_explanation: str,
        source_text: Optional[str] = None,
        source_start: Optional[int] = None,
        source_end: Optional[int] = None,
        source_page_number: Optional[int] = None,
        source_video_timestamp_start_seconds: Optional[int] = None,
        source_video_timestamp_end_seconds: Optional[int] = None
    ) -> Optional[int]:
        """Add a new key concept associated with a file.
        
        Args:
            file_id: ID of the file this concept belongs to
            concept_title: Title/name of the concept
            concept_explanation: Detailed explanation of the concept
            source_text: Text span from which the concept was derived
            source_start: Start position of the text span
            source_end: End position of the text span
            source_page_number: Page number for PDF sources
            source_video_timestamp_start_seconds: Start timestamp for video sources
            source_video_timestamp_end_seconds: End timestamp for video sources
            
        Returns:
            int: The ID of the newly created concept, or None if creation failed
        """
        session = self.get_session()
        try:
            new_concept = KeyConceptORM(
                file_id=file_id,
                concept=concept_title,
                explanation=concept_explanation,
                span_text=source_text,
                span_start=source_start,
                span_end=source_end,
                source_page_number=source_page_number,
                source_video_timestamp_start_seconds=source_video_timestamp_start_seconds,
                source_video_timestamp_end_seconds=source_video_timestamp_end_seconds
            )
            session.add(new_concept)
            session.commit()
            logger.info(f"Added key concept '{concept_title}' for file {file_id}")
            return new_concept.id
        except Exception as e:
            session.rollback()
            logger.error(f"Error adding key concept: {e}", exc_info=True)
            return None
        finally:
            session.close()
    
    # Flashcard functionality removed as it no longer exists in the DB schema
    
    # Quiz question functionality removed as it no longer exists in the DB schema
    
    def get_key_concepts_for_file(self, file_id: int) -> List[Dict[str, Any]]:
        """Get key concepts for a file using robust querying.
        
        Args:
            file_id: ID of the file
            
        Returns:
            List[Dict]: List of key concepts
        """
        session = self.get_session()
        try:
            try:
                # Try regular ORM query first
                concepts_orm = session.query(KeyConceptORM).filter(
                    KeyConceptORM.file_id == file_id
                ).all()
                
                result = []
                for concept in concepts_orm:
                    result.append({
                        'id': concept.id,
                        'file_id': concept.file_id,
                        'concept': concept.concept,
                        'explanation': concept.explanation,
                        'span_text': concept.span_text,
                        'span_start': concept.span_start,
                        'span_end': concept.span_end,
                        'source_page_number': concept.source_page_number,
                        'source_video_timestamp_start_seconds': concept.source_video_timestamp_start_seconds,
                        'source_video_timestamp_end_seconds': concept.source_video_timestamp_end_seconds
                    })
                
                return result
                
            except Exception as orm_error:
                logger.warning(f"ORM query for key concepts failed: {orm_error}. Trying direct SQL.")
                
                # Fallback to direct SQL if ORM query fails
                try:
                    # Direct SQL query to get key concepts
                    result = session.execute(text(
                        "SELECT id, file_id, concept, explanation, span_text, span_start, span_end, "
                        "source_page_number, source_video_timestamp_start_seconds, source_video_timestamp_end_seconds "
                        f"FROM key_concepts WHERE file_id = {file_id}"
                    ))
                    
                    # Create dictionaries from the SQL result
                    key_concepts = []
                    for row in result:
                        kc = {
                            'id': row[0],
                            'file_id': row[1],
                            'concept': row[2],
                            'explanation': row[3],
                            'span_text': row[4],
                            'span_start': row[5],
                            'span_end': row[6],
                            'source_page_number': row[7],
                            'source_video_timestamp_start_seconds': row[8],
                            'source_video_timestamp_end_seconds': row[9]
                        }
                        key_concepts.append(kc)
                    
                    return key_concepts
                    
                except Exception as sql_error:
                    logger.error(f"Direct SQL query for key concepts failed: {sql_error}")
                    return []
                    
        except Exception as e:
            logger.error(f"Error getting key concepts: {e}", exc_info=True)
            return []
        finally:
            session.close()
    
    # Flashcard retrieval functionality removed as it no longer exists in the DB schema
    
    # Quiz question retrieval functionality removed as it no longer exists in the DB schema
