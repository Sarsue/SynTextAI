"""
Summarization Agent for generating structured summaries and key concepts.

This agent processes content chunks and generates comprehensive summaries,
extracts key concepts, and provides structured outputs for further analysis.
"""
import logging
from typing import Dict, Any, List, Optional
from pydantic import Field

from sqlalchemy.orm import Session
from typing import Optional

from .base_agent import BaseAgent, AgentConfig, AgentError
from .dspy_utils import extract_key_concepts
from ..models.orm_models import KeyConcept

logger = logging.getLogger(__name__)

class SummarizationConfig(AgentConfig):
    """Configuration for the Summarization Agent."""
    max_summary_length: int = 1000
    max_concepts: int = 10
    language: str = "English"
    comprehension_level: str = "intermediate"  # beginner, intermediate, advanced
    include_bullet_points: bool = True
    include_citations: bool = True
    temperature: float = 0.3  # Lower temperature for more focused, deterministic outputs

@agent("summarization", {
    "max_summary_length": 1000,
    "max_concepts": 10,
    "language": "English",
    "comprehension_level": "intermediate"
})
class SummarizationAgent(BaseAgent[SummarizationConfig]):
    """
    Agent responsible for generating structured summaries and key concepts.
    
    This agent processes content chunks and generates comprehensive summaries,
    extracts key concepts, and provides structured outputs for further analysis.
    """
    
    def __init__(self, config: Optional[SummarizationConfig] = None):
        super().__init__(config or SummarizationConfig())
        self.supported_languages = ["english", "spanish", "french"]  # Supported languages
        self.supported_levels = ["beginner", "intermediate", "advanced"]
    
    async def process(self, input_data: Dict[str, Any], db: Optional[Session] = None) -> Dict[str, Any]:
        """
        Process the input content and generate a structured summary.
        
        Args:
            input_data: Dictionary containing:
                - content: The content to summarize (string or list of chunks)
                - file_id: Optional ID of the file these concepts belong to
                - language: Optional language override
                - comprehension_level: Optional level override
                - Additional metadata
            db: Optional SQLAlchemy session for database operations
                
        Returns:
            Dictionary containing the structured summary and key concepts
        """
        try:
            # Validate input
            await self.validate_input(input_data)
            
            # Extract content and metadata
            content = input_data.get("content", "")
            file_id = input_data.get("file_id")
            language = input_data.get("language", self.config.language)
            level = input_data.get("comprehension_level", self.config.comprehension_level)
            
            # Generate summary
            summary_result = await self._generate_summary(
                content=content,
                language=language,
                level=level
            )
            
            # Extract key concepts
            concepts_result = await self._extract_key_concepts(
                content=content,
                language=language,
                level=level,
                db=db,
                file_id=file_id
            )
            
            return {
                "status": "success",
                "summary": summary_result,
                "key_concepts": concepts_result,
                "file_id": file_id
            }
            
        except Exception as e:
            logger.error(f"Error in summarization: {str(e)}", exc_info=True)
            raise AgentError(f"Failed to generate summary: {str(e)}")
    
    async def _generate_summary(
        self, 
        content: Any, 
        language: str,
        level: str
    ) -> Dict[str, Any]:
        """Generate a structured summary of the content."""
        try:
            # Convert content to string if it's a list of chunks
            if isinstance(content, list):
                content = "\n\n".join(
                    chunk.get("text", "") if isinstance(chunk, dict) else str(chunk)
                    for chunk in content
                )
            
            # Prepare prompt based on content type and requirements
            prompt = self._build_summary_prompt(content, language, level)
            
            # Call LLM to generate summary
            # Note: Replace this with actual LLM call
            summary = f"Generated summary for {len(content)} characters of content in {language} at {level} level."
            
            # Structure the summary with sections if needed
            structured_summary = {
                "overview": summary,
                "sections": [],
                "length": len(summary),
                "language": language,
                "comprehension_level": level
            }
            
            return structured_summary
            
        except Exception as e:
            logger.error(f"Error generating summary: {str(e)}", exc_info=True)
            raise AgentError(f"Failed to generate summary: {str(e)}")
    
    async def _extract_key_concepts(
        self, 
        content: Any, 
        language: str,
        level: str,
        db: Optional[Session] = None,
        file_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Extract key concepts from the content using DSPy.
        
        Args:
            content: The content to extract concepts from (string or list of chunks)
            language: Language of the content (e.g., 'english', 'spanish')
            level: Comprehension level ('beginner', 'intermediate', 'advanced')
            
        Returns:
            List of key concepts with titles, explanations, and metadata
            
        Raises:
            AgentError: If key concept extraction fails
        """
        try:
            # Convert content to string if it's a list of chunks
            if isinstance(content, list):
                content = "\n\n".join(
                    chunk.get("text", "") if isinstance(chunk, dict) else str(chunk)
                    for chunk in content
                )
            
            # Use DSPy-based key concept extraction
            concepts = extract_key_concepts(
                document=content,
                language=language.lower(),
                comprehension_level=level.lower()
            )
            
            # Format concepts into a consistent structure and save to DB if session is provided
            formatted_concepts = []
            db_concepts = []
            
            for concept in concepts:
                if not isinstance(concept, dict):
                    continue
                
                # Create concept dictionary for response
                formatted_concept = {
                    "concept_title": concept.get("concept_title", "").strip(),
                    "concept_explanation": concept.get("concept_explanation", "").strip(),
                    "confidence": float(concept.get("confidence", 0.0)),
                    "is_custom": False,  # Mark as auto-generated
                    "source_page_number": concept.get("source_page_number"),
                    "source_video_timestamp_start_seconds": concept.get("source_video_timestamp_start_seconds"),
                    "source_video_timestamp_end_seconds": concept.get("source_video_timestamp_end_seconds")
                }
                
                # Skip empty concepts
                if not formatted_concept["concept_title"] or not formatted_concept["concept_explanation"]:
                    continue
                
                formatted_concepts.append(formatted_concept)
                
                # Create database object if session is provided
                if db is not None and file_id is not None:
                    db_concept = KeyConcept(
                        file_id=file_id,
                        concept_title=formatted_concept["concept_title"],
                        concept_explanation=formatted_concept["concept_explanation"],
                        source_page_number=formatted_concept["source_page_number"],
                        source_video_timestamp_start_seconds=formatted_concept["source_video_timestamp_start_seconds"],
                        source_video_timestamp_end_seconds=formatted_concept["source_video_timestamp_end_seconds"],
                        is_custom=False
                    )
                    db_concepts.append(db_concept)
            
            # Sort concepts by confidence (highest first)
            formatted_concepts.sort(key=lambda x: x.get("confidence", 0.0), reverse=True)
            
            # Limit to max_concepts if specified
            if hasattr(self.config, 'max_concepts') and self.config.max_concepts:
                formatted_concepts = formatted_concepts[:self.config.max_concepts]
                db_concepts = db_concepts[:self.config.max_concepts]
            
            # Save to database if session is provided
            if db is not None and file_id is not None and db_concepts:
                try:
                    # Delete existing auto-generated concepts for this file
                    db.query(KeyConcept).filter(
                        KeyConcept.file_id == file_id,
                        KeyConcept.is_custom == False
                    ).delete(synchronize_session=False)
                    
                    # Add new concepts
                    db.add_all(db_concepts)
                    db.commit()
                    
                    # Update formatted_concepts with database IDs
                    for i, db_concept in enumerate(db_concepts):
                        if i < len(formatted_concepts):
                            formatted_concepts[i]["id"] = db_concept.id
                except Exception as e:
                    db.rollback()
                    logger.error(f"Error saving key concepts to database: {str(e)}", exc_info=True)
                    # Continue with in-memory concepts even if DB save fails
            
            return formatted_concepts
            
        except Exception as e:
            logger.error(f"Error extracting key concepts: {str(e)}", exc_info=True)
            raise AgentError(f"Failed to extract key concepts: {str(e)}")
    
    def _build_summary_prompt(
        self, 
        content: str, 
        language: str,
        level: str
    ) -> str:
        """Build the prompt for summary generation."""
        # This is a simplified version - in practice, you'd want more sophisticated prompt engineering
        return (
            f"Generate a {level}-level summary in {language} for the following content. "
            f"The summary should be comprehensive but concise, suitable for {level} understanding.\n\n"
            f"CONTENT:\n{content[:10000]}..."  # Limit content length for the prompt
        )
    
    async def validate_input(self, input_data: Dict[str, Any]) -> bool:
        """Validate the input data before processing."""
        if not input_data.get("content"):
            raise AgentError("No content provided for summarization")
        
        language = input_data.get("language", self.config.language).lower()
        if language not in [lang.lower() for lang in self.supported_languages]:
            logger.warning(
                f"Language '{language}' may not be fully supported. "
                f"Supported languages: {', '.join(self.supported_languages)}"
            )
        
        level = input_data.get("comprehension_level", self.config.comprehension_level).lower()
        if level not in self.supported_levels:
            raise AgentError(
                f"Unsupported comprehension level: {level}. "
                f"Supported levels: {', '.join(self.supported_levels)}"
            )
        
        return True
