"""
Quiz Agent for generating and managing quizzes from content.

This agent handles the generation of quiz questions from content, supporting
multiple question types and difficulty levels. It's designed to work with the
files API to generate and store quiz questions for specific files.

Key Features:
- Generates multiple question types (MCQ, True/False, short answer)
- Supports different difficulty levels
- Includes explanations for answers
- Tracks source content for each question
"""
from typing import Dict, Any, List, Optional, Union, cast
import json
import logging
import random
from pydantic import BaseModel, Field, validator

from sqlalchemy.orm import Session
from typing import Dict, Any, List, Optional, Union, cast
import json
import logging
import random
from pydantic import BaseModel, Field, validator

from .base_agent import BaseAgent, AgentConfig
from .prompt_loader import PromptLoader
from ..services.llm_service import llm_service
from ..models.orm_models import KeyConcept

logger = logging.getLogger(__name__)

class QuizConfig(AgentConfig):
    """Configuration for the Quiz Agent.
    
    Attributes:
        num_questions: Number of questions to generate (1-20)
        difficulty: Difficulty level (easy, medium, hard)
        question_types: List of question types to generate
        include_explanations: Whether to include answer explanations
        max_flashcards: Maximum number of flashcards to generate
        flashcard_types: Types of flashcards to generate (basic, mcq, true_false)
    """
    max_flashcards: int = Field(
        default=10,
        description="Maximum number of flashcards to generate",
        ge=1,
        le=50
    )
    flashcard_types: List[str] = Field(
        default_factory=lambda: ["basic", "mcq", "true_false"],
        description="Types of flashcards to generate"
    )
    num_questions: int = Field(
        default=5,
        description="Number of questions to generate per quiz",
        ge=1,
        le=20
    )
    difficulty: str = Field(
        default="medium",
        description="Difficulty level of the quiz questions",
        enum=["easy", "medium", "hard"]
    )
    question_types: List[str] = Field(
        default_factory=lambda: ["MCQ", "true_false", "short_answer"],
        description="Types of questions to include in the quiz"
    )
    include_explanations: bool = Field(
        default=True,
        description="Whether to include explanations for the correct answers"
    )

class QuizQuestion(BaseModel):
    """Model representing a single quiz question.
    
    This matches the expected format in the database and frontend.
    """
    question: str
    question_type: str = "MCQ"  # Default to MCQ for backward compatibility
    options: List[str] = Field(default_factory=list, alias="distractors")
    correct_answer: str
    explanation: str = ""
    difficulty: str = "medium"
    key_concept_id: Optional[int] = None
    source_page: Optional[int] = None
    source_text: Optional[str] = None
    
    class Config:
        allow_population_by_field_name = True
        
    @validator('question_type')
    def validate_question_type(cls, v):
        valid_types = ["MCQ", "true_false", "short_answer", "multiple_choice"]
        if v.lower() not in [t.lower() for t in valid_types]:
            raise ValueError(f"Invalid question type. Must be one of: {', '.join(valid_types)}")
        # Standardize to uppercase for consistency
        return v.upper() if v.upper() in ["MCQ", "TRUE_FALSE"] else v.lower()

class QuizResult(BaseModel):
    """Model representing a complete quiz."""
    title: str
    description: str
    questions: List[QuizQuestion]
    total_questions: int
    estimated_time_minutes: int

class QuizAgent(BaseAgent[QuizConfig]):
    """Agent for generating and managing quizzes from content.
    
    This agent handles the generation of quiz questions from document content,
    supporting multiple question types and difficulty levels. It's designed to
    work with the files API to generate and store quiz questions.
    """
    
    @classmethod
    def get_default_config(cls) -> QuizConfig:
        """Return the default configuration for this agent."""
        return QuizConfig()
    
    async def process(self, input_data: Dict[str, Any], db: Optional[Session] = None) -> Dict[str, Any]:
        """
        Process quiz or flashcard generation request.
        
        Expected input format for quizzes:
        {
            "action": "generate_questions",
            "file_id": int,  # Required for database lookup
            "file_content": str,
            "key_concepts": Optional[List[Dict]],  # Will be fetched from DB if not provided
            "count": int,
            "difficulty": str,
            "question_types": List[str]
        }
        
        Expected input format for flashcards:
        {
            "action": "generate_flashcards",
            "file_id": int,  # Required for database lookup
            "key_concepts": Optional[List[Dict]],  # Will be fetched from DB if not provided
            "count": int,
            "flashcard_types": List[str]  # basic, mcq, true_false
        }
        
        Args:
            input_data: Dictionary containing input parameters
            db: Optional SQLAlchemy session for database operations
            
        Returns:
            Dictionary containing the generated questions in the format expected by the frontend
        """
        try:
            action = input_data.get("action", "generate_questions")
            
            if action == "generate_questions":
                return await self._generate_quiz_questions(input_data, db)
            elif action == "generate_flashcards":
                return await self._generate_flashcards(input_data, db)
            else:
                raise ValueError(f"Unsupported action: {action}")
            
        except Exception as e:
            logger.error(f"Error generating quiz: {str(e)}", exc_info=True)
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def _prepare_prompt(self, input_data: Dict[str, Any], db: Optional[Session] = None) -> str:
        """Prepare the prompt for the LLM.
        
        Args:
            input_data: Dictionary containing the input data for the prompt
            db: Optional SQLAlchemy session for database operations
            
        Returns:
            Formatted prompt string with key concepts from the database
            
        Note:
            If key_concepts are provided in input_data, they'll be used directly.
            Otherwise, key concepts will be fetched from the database.
        """
        content = input_data.get("file_content", "")
        file_id = input_data.get("file_id")
        
        # Use provided key concepts, fetch from DB, or use empty list
        if "key_concepts" in input_data and input_data["key_concepts"]:
            # Use provided key concepts
            key_concepts = "\n".join(
                f"- {kc.get('concept_title', kc.get('title', ''))}: {kc.get('concept_explanation', kc.get('explanation', ''))}"
                for kc in input_data["key_concepts"]
            )
        elif db is not None and file_id is not None:
            try:
                # Fetch key concepts from database
                concepts = db.query(KeyConcept).filter(
                    KeyConcept.file_id == file_id,
                    KeyConcept.is_custom == False  # Only use auto-generated concepts
                ).order_by(KeyConcept.id.desc()).limit(10).all()  # Get most recent 10
                
                key_concepts = "\n".join(
                    f"- {concept.concept_title}: {concept.concept_explanation}"
                    for concept in concepts
                )
                logger.info(f"Fetched {len(concepts)} key concepts from database for file {file_id}")
            except Exception as e:
                logger.warning(f"Failed to fetch key concepts from database: {e}")
                key_concepts = ""
        else:
            logger.warning("No database session or file_id provided, using empty key concepts")
            key_concepts = ""
        
        return PromptLoader.render_instruction(
            "quiz",
            content=content,
            key_concepts=key_concepts,
            num_questions=self.config.num_questions,
            difficulty=self.config.difficulty,
            question_types=", ".join(self.config.question_types),
            include_explanations=self.config.include_explanations
        )
    
    async def _call_llm(self, prompt: str) -> str:
        """Call the LLM with the given prompt to generate quiz questions.
        
        Args:
            prompt: The formatted prompt to send to the LLM
            
        Returns:
            Raw LLM response as a string containing the generated questions in JSON format
            
        Raises:
            ValueError: If the LLM call fails or returns an invalid response
        """
        try:
            # Call the LLM service with appropriate parameters
            response = await llm_service.generate_text(
                prompt=prompt,
                temperature=0.7,  # Slightly higher temperature for more varied questions
                max_tokens=2000,  # Enough for multiple questions
                top_p=0.9,
                frequency_penalty=0.5,  # Encourage diverse questions
                presence_penalty=0.5
            )
            
            logger.debug(f"LLM response: {response[:200]}...")  # Log first 200 chars
            return response
            
        except Exception as e:
            logger.error(f"Error calling LLM service: {str(e)}", exc_info=True)
            raise ValueError(f"Failed to generate quiz questions: {str(e)}")
    
    async def _generate_quiz_questions(self, input_data: Dict[str, Any], db: Optional[Session] = None) -> Dict[str, Any]:
        """Generate quiz questions from content."""
        if not input_data.get("file_content"):
            raise ValueError("Input must contain 'file_content' for quiz generation")
            
        # Update config from input data if provided
        if "count" in input_data:
            self.config.num_questions = input_data["count"]
        if "difficulty" in input_data:
            self.config.difficulty = input_data["difficulty"]
        if "question_types" in input_data:
            self.config.question_types = input_data["question_types"]
        
        # Prepare the prompt with key concepts from DB if available
        prompt = await self._prepare_prompt(input_data, db)
        
        # Call LLM to generate quiz
        llm_response = await self._call_llm(prompt)
        
        # Parse and validate the response
        questions = self._parse_llm_response(llm_response)
        
        # Format response to match frontend expectations
        return {
            "status": "success",
            "questions": [q.dict(by_alias=True) for q in questions],
            "file_id": input_data.get("file_id")
        }

    async def _generate_flashcards(self, input_data: Dict[str, Any], db: Optional[Session] = None) -> Dict[str, Any]:
        """Generate flashcards from key concepts."""
        file_id = input_data.get("file_id")
        if not file_id:
            raise ValueError("file_id is required for flashcard generation")
            
        # Update config from input data if provided
        if "count" in input_data:
            self.config.max_flashcards = input_data["count"]
        if "flashcard_types" in input_data:
            self.config.flashcard_types = input_data["flashcard_types"]
            
        # Get key concepts from input or database
        key_concepts = input_data.get("key_concepts")
        if key_concepts is None and db is not None:
            key_concepts = await self._get_key_concepts_from_db(db, file_id)
        
        if not key_concepts:
            return {
                "status": "success",
                "flashcards": [],
                "message": "No key concepts found for flashcard generation"
            }
            
        # Generate flashcards
        flashcards = []
        for concept in key_concepts[:self.config.max_flashcards]:
            if "basic" in self.config.flashcard_types:
                flashcards.append(self._generate_basic_flashcard(concept))
            if "mcq" in self.config.flashcard_types and len(key_concepts) > 1:
                flashcards.append(self._generate_mcq_flashcard(concept, key_concepts))
            if "true_false" in self.config.flashcard_types and len(key_concepts) > 1:
                flashcards.append(self._generate_true_false_flashcard(concept, key_concepts))
        
        return {
            "status": "success",
            "flashcards": flashcards,
            "file_id": file_id
        }
    
    async def _get_key_concepts_from_db(self, db: Session, file_id: int) -> List[Dict[str, Any]]:
        """Fetch key concepts from the database for a given file."""
        try:
            concepts = db.query(KeyConcept).filter(
                KeyConcept.file_id == file_id,
                KeyConcept.is_custom == False  # Only use auto-generated concepts
            ).order_by(KeyConcept.id.desc()).limit(20).all()  # Get most recent 20 concepts
            
            return [{
                "id": str(concept.id),
                "concept_title": concept.concept_title,
                "concept_explanation": concept.concept_explanation,
                "source_page": concept.source_page_number,
                "source_timestamp_start": concept.source_video_timestamp_start_seconds,
                "source_timestamp_end": concept.source_video_timestamp_end_seconds
            } for concept in concepts]
            
        except Exception as e:
            logger.error(f"Error fetching key concepts from database: {str(e)}")
            return []
    
    def _generate_basic_flashcard(self, concept: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a basic Q&A flashcard from a key concept."""
        return {
            "type": "basic",
            "question": f"What is {concept['concept_title']}?",
            "answer": concept["concept_explanation"],
            "key_concept_id": concept.get("id"),
            "source_page": concept.get("source_page"),
            "source_timestamp_start": concept.get("source_timestamp_start"),
            "source_timestamp_end": concept.get("source_timestamp_end")
        }
    
    def _generate_mcq_flashcard(self, concept: Dict[str, Any], all_concepts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate a multiple-choice flashcard from a key concept."""
        correct_answer = concept["concept_explanation"]
        distractors = [
            c["concept_explanation"] 
            for c in all_concepts 
            if c.get("id") != concept.get("id")
        ]
        random.shuffle(distractors)
        distractors = distractors[:2]  # Use 2 distractors
        options = distractors + [correct_answer]
        random.shuffle(options)
        
        return {
            "type": "mcq",
            "question": f"Which of the following best describes '{concept['concept_title']}'?",
            "options": options,
            "correct_answer": correct_answer,
            "key_concept_id": concept.get("id"),
            "source_page": concept.get("source_page"),
            "source_timestamp_start": concept.get("source_timestamp_start"),
            "source_timestamp_end": concept.get("source_timestamp_end")
        }
    
    def _generate_true_false_flashcard(self, concept: Dict[str, Any], all_concepts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate a true/false flashcard from a key concept."""
        use_true = random.choice([True, False])
        
        if use_true or len(all_concepts) == 1:
            statement = concept["concept_explanation"]
            is_true = True
        else:
            # Pick a random explanation from another concept
            distractors = [c for c in all_concepts if c.get("id") != concept.get("id")]
            if distractors:
                distractor = random.choice(distractors)
                statement = distractor["concept_explanation"]
                is_true = False
            else:
                statement = concept["concept_explanation"]
                is_true = True
        
        return {
            "type": "true_false",
            "question": f"True or False: '{concept['concept_title']}' is defined as: {statement}",
            "correct_answer": is_true,
            "key_concept_id": concept.get("id"),
            "source_page": concept.get("source_page"),
            "source_timestamp_start": concept.get("source_timestamp_start"),
            "source_timestamp_end": concept.get("source_timestamp_end")
        }
    
    def _parse_llm_response(self, response: str) -> List[QuizQuestion]:
        """Parse and validate the LLM response for quiz questions."""
        try:
            # Parse JSON response
            data = json.loads(response)
            
            # Basic validation
            if not isinstance(data, dict):
                raise ValueError("Invalid response format: expected JSON object")
                
            if "questions" not in data or not isinstance(data["questions"], list):
                raise ValueError("Invalid response format: missing or invalid 'questions' field")
            
            questions = []
            for i, q_data in enumerate(data["questions"], 1):
                try:
                    # Convert question data to QuizQuestion model
                    # This will validate the data structure
                    question = QuizQuestion(**q_data)
                    questions.append(question)
                except Exception as e:
                    logger.warning(f"Skipping invalid question {i}: {str(e)}")
                    continue
            
            if not questions:
                raise ValueError("No valid questions found in the response")
                
            return questions
            
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse LLM response: {str(e)}") from e
