"""
Agent Service for MCP Agent Integration

This module provides a service layer between API routes and MCP agents,
handling input validation, agent invocation, and response formatting.
"""
from typing import Dict, Any, Optional, List
from fastapi import HTTPException, status
from api.agents.agent_factory import AgentFactory
import logging

logger = logging.getLogger(__name__)

class AgentService:
    """Service for interacting with MCP agents."""
    
    def __init__(self):
        self.agent_factory = AgentFactory()
    
    async def process_content(
        self,
        agent_name: str,
        content: str,
        content_type: str = "text",
        language: str = "en",
        comprehension_level: str = "beginner",
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Process content using the specified agent.
        
        Args:
            agent_name: Name of the agent to use (ingestion, summarization, etc.)
            content: The content to process
            content_type: Type of content (text, url, pdf, etc.)
            language: Language code (default: "en")
            comprehension_level: User's comprehension level (beginner, intermediate, advanced)
            metadata: Additional metadata for processing
            **kwargs: Additional agent-specific parameters
            
        Returns:
            Dict containing the processing results
            
        Raises:
            HTTPException: If there's an error processing the content
        """
        try:
            logger.info(f"Initializing agent: {agent_name}")
            # Get the appropriate agent asynchronously
            try:
                agent = await self.agent_factory.get_agent(agent_name)
                if not agent:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Unsupported agent type: {agent_name}"
                    )
                logger.info(f"Successfully initialized agent: {agent_name}")
            except Exception as e:
                logger.error(f"Failed to initialize agent {agent_name}: {str(e)}", exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to initialize agent {agent_name}: {str(e)}"
                )
            
            # Prepare input data for the agent
            input_data = {
                "content": content,
                "content_type": content_type,
                "language": language,
                "comprehension_level": comprehension_level,
                "metadata": metadata or {},
                **kwargs
            }
            
            logger.debug(f"Processing content with {agent_name} agent")
            # Process the content asynchronously
            try:
                result = await agent.process(input_data)
                
                # Ensure the result has the expected structure
                if not isinstance(result, dict) or "status" not in result:
                    logger.warning(f"Unexpected response format from {agent_name} agent")
                    result = {
                        "status": "success",
                        "result": result
                    }
                
                return result
                
            except Exception as e:
                logger.error(f"Error in {agent_name} agent processing: {str(e)}", exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Agent processing error: {str(e)}"
                )
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error in process_content: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error processing content: {str(e)}"
            )
    
    async def generate_key_concepts(
        self,
        content: str,
        content_type: str = "text",
        language: str = "en",
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Generate key concepts from content.
        
        Args:
            content: The content to analyze
            content_type: Type of content (text, url, pdf, etc.)
            language: Language code (default: "en")
            **kwargs: Additional parameters for concept generation
            
        Returns:
            List of key concepts with explanations and source locations
        """
        result = await self.process_content(
            agent_name="ingestion",
            content=content,
            content_type=content_type,
            language=language,
            task="extract_concepts",
            **kwargs
        )
        
        if result.get("status") != "success":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=result.get("error", "Failed to generate key concepts")
            )
            
        return result.get("concepts", [])
    
    async def generate_study_materials(
        self,
        content: str,
        content_type: str = "text",
        language: str = "en",
        comprehension_level: str = "beginner",
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate study materials (flashcards, quizzes) from content.
        
        Args:
            content: The content to analyze
            content_type: Type of content (text, url, pdf, etc.)
            language: Language code (default: "en")
            comprehension_level: User's comprehension level
            **kwargs: Additional parameters for study material generation
            
        Returns:
            Dict containing generated study materials
        """
        # First extract key concepts
        concepts = await self.generate_key_concepts(
            content=content,
            content_type=content_type,
            language=language,
            **kwargs
        )
        
        # Then generate study materials based on concepts
        materials = {}
        
        # Generate flashcards
        flashcard_result = await self.process_content(
            agent_name="quiz",
            content={"concepts": concepts},
            content_type="json",
            language=language,
            comprehension_level=comprehension_level,
            material_type="flashcards",
            **kwargs
        )
        
        if flashcard_result.get("status") == "success":
            materials["flashcards"] = flashcard_result.get("flashcards", [])
        
        # Generate quizzes
        quiz_result = await self.process_content(
            agent_name="quiz",
            content={"concepts": concepts},
            content_type="json",
            language=language,
            comprehension_level=comprehension_level,
            material_type="quizzes",
            **kwargs
        )
        
        if quiz_result.get("status") == "success":
            materials["quizzes"] = quiz_result.get("quizzes", [])
        
        return {
            "status": "success",
            "materials": materials,
            "concepts": concepts
        }

# Singleton instance
agent_service = AgentService()
