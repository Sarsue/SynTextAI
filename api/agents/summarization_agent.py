"""
Summarization Agent for generating structured summaries and key concept extraction.

This module provides the SummarizationAgent class which is responsible for processing
content and generating comprehensive summaries, extracting key concepts, and providing
structured outputs for further analysis in the SynTextAI system.

The agent handles:
- Content summarization at different comprehension levels
- Key concept extraction with metadata
- Language support for multiple languages
- Integration with the DSPy framework for advanced NLP tasks
- Structured output generation for downstream processing

Example Usage:
    ```python
    # Initialize the agent
    agent = SummarizationAgent()
    
    # Summarize content with custom settings
    result = await agent.process({
        "content": "Long form content to summarize...",
        "file_id": "doc-123",
        "language": "english",
        "comprehension_level": "intermediate",
        "max_summary_length": 1000,
        "max_concepts": 10
    })
    
    # Access the results
    summary = result["summary"]
    key_concepts = result["key_concepts"]
    ```
"""
import logging
from typing import Dict, Any, List, Optional

from sqlalchemy.orm import Session
from typing import Optional

from .base_agent import BaseAgent, AgentConfig, AgentError
from .dspy_utils import extract_key_concepts
from ..models.orm_models import KeyConcept

logger = logging.getLogger(__name__)

class SummarizationConfig(AgentConfig):
    """
    Configuration for the Summarization Agent.
    
    This configuration class controls how the SummarizationAgent processes content
    and generates summaries and key concepts.
    
    Attributes:
        max_summary_length: Maximum length of the generated summary in characters.
                           Default: 1000
        max_concepts: Maximum number of key concepts to extract.
                     Default: 10
        language: Default language for summarization.
                 Default: "English"
        comprehension_level: Target comprehension level for the summary.
                           One of: "beginner", "intermediate", "advanced"
                           Default: "intermediate"
        include_bullet_points: Whether to include bullet points in the summary.
                             Default: True
        include_citations: Whether to include citations in the summary.
                         Default: True
        temperature: Controls randomness in the LLM output.
                   Lower values make outputs more deterministic.
                   Range: 0.0 to 2.0
                   Default: 0.3 (more focused, deterministic outputs)
    """
    max_summary_length: int = 1000
    max_concepts: int = 10
    language: str = "English"
    comprehension_level: str = "intermediate"
    include_bullet_points: bool = True
    include_citations: bool = True
    temperature: float = 0.3

@agent("summarization", {
    "max_summary_length": 1000,
    "max_concepts": 10,
    "language": "English",
    "comprehension_level": "intermediate"
})
class SummarizationAgent(BaseAgent[SummarizationConfig]):
    """
    Agent for generating structured summaries and extracting key concepts from content.
    
    The SummarizationAgent is a core component of SynTextAI that processes content
    and generates comprehensive summaries at different comprehension levels while
    extracting and structuring key concepts for enhanced learning and analysis.
    
    Key Features:
    - Multi-level summarization (beginner, intermediate, advanced)
    - Key concept extraction with relevance scoring
    - Support for multiple languages
    - Integration with DSPy for advanced NLP capabilities
    - Configurable output formatting
    
    The agent uses a two-step process:
    1. Generates a comprehensive summary of the input content
    2. Extracts and structures key concepts with metadata
    
    Example:
        ```python
        # Initialize with custom configuration
        config = SummarizationConfig(
            max_summary_length=1500,
            max_concepts=15,
            language="english",
            comprehension_level="intermediate",
            temperature=0.3
        )
        agent = SummarizationAgent(config)
        
        # Process content
        result = await agent.process({
            "content": "Long form content to analyze...",
            "file_id": "doc-123",
            "metadata": {"title": "Sample Content"}
        })
        
        # Access results
        summary = result["summary"]
        concepts = result["key_concepts"]
        ```
    """
    
    def __init__(self, config: Optional[SummarizationConfig] = None):
        super().__init__(config or SummarizationConfig())
        self.supported_languages = ["english", "spanish", "french"]  # Supported languages
        self.supported_levels = ["beginner", "intermediate", "advanced"]
    
    async def process(self, input_data: Dict[str, Any], db: Optional[Session] = None) -> Dict[str, Any]:
        """
        Process the input content and generate a structured summary with key concepts.
        
        This is the main entry point for the SummarizationAgent. It processes the input
        content, generates a summary at the specified comprehension level, and extracts
        key concepts with metadata.
        
        Args:
            input_data: Dictionary containing:
                content (str|List[str]): The content to summarize. Can be a single string
                                       or a list of content chunks.
                file_id (str, optional): ID of the file these concepts belong to.
                language (str, optional): Language code for the summary (e.g., "english",
                                        "spanish"). Overrides the default from config.
                comprehension_level (str, optional): Target comprehension level.
                                                   One of: "beginner", "intermediate", "advanced".
                                                   Overrides the default from config.
                max_summary_length (int, optional): Maximum length of the summary in characters.
                                                  Overrides the default from config.
                max_concepts (int, optional): Maximum number of key concepts to extract.
                                            Overrides the default from config.
                metadata (dict, optional): Additional metadata about the content.
                
            db (Session, optional): SQLAlchemy session for database operations.
                                  Required if storing concepts in the database.
                                  
        Returns:
            Dict[str, Any]: A dictionary containing:
                - status (str): "success" or "error"
                - summary (dict): Generated summary with metadata
                - key_concepts (list): Extracted key concepts with metadata
                - file_id (str): The file ID if provided in input
                - error (str, optional): Error message if processing failed
                
        Raises:
            AgentError: If input validation fails or processing encounters an error
            
        Example:
            ```python
            result = await agent.process({
                "content": "Long form content...",
                "file_id": "doc-123",
                "language": "english",
                "comprehension_level": "intermediate",
                "metadata": {"title": "Sample Document"}
            })
            ```
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
            self._build_summary_prompt(content, language, level)
            
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
