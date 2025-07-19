# mcp_service.py
import os
import logging
import asyncio
import tempfile
from typing import List, Dict, Optional, AsyncGenerator, Union, Tuple
from pathlib import Path

import google.generativeai as genai
import dspy
from dspy.retrieve.pgvector_rm import PgVectorRM
from faster_whisper import WhisperModel
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential
from typing import Optional, List, Dict, Any

# Configure logging
logger = logging.getLogger(__name__)

# Set up cache directory in user's home folder
CACHE_DIR = os.path.expanduser("~/.cache/syntextai")
os.makedirs(CACHE_DIR, exist_ok=True)

# Set environment variables for model cache
os.environ["HF_HOME"] = os.path.join(CACHE_DIR, "huggingface")
os.environ["TRANSFORMERS_CACHE"] = os.path.join(CACHE_DIR, "huggingface/transformers")
os.makedirs(os.environ["HF_HOME"], exist_ok=True)
os.makedirs(os.environ["TRANSFORMERS_CACHE"], exist_ok=True)

class MCPModelConfig(BaseModel):
    """Configuration for an MCP model."""
    model_name: str
    provider: str
    version: str = "1.0"
    max_retries: int = 3
    timeout: int = 30
    temperature: float = 0.7
    max_tokens: int = 2048
    top_p: float = 1.0
    top_k: int = 40
    device: str = "cpu"
    compute_type: str = "int8"  # For Whisper

class MCPService:
    def __init__(self):
        self.models = {
            "concept_extractor": MCPModelConfig(
                model_name="gemini-1.5-flash",
                provider="google",
                temperature=0.2
            ),
            "explanation_generator": MCPModelConfig(
                model_name="gemini-1.5-flash",
                provider="google",
                temperature=0.7
            ),
            "quiz_generator": MCPModelConfig(
                model_name="gemini-1.5-flash",
                provider="google",
                temperature=0.8
            ),
            "flashcard_generator": MCPModelConfig(
                model_name="gemini-1.5-flash",
                provider="google",
                temperature=0.5
            ),
            "speech_to_text": MCPModelConfig(
                model_name="small",
                provider="whisper",
                temperature=0.0,
                device="cpu",
                compute_type="int8"
            ),
            "chat_with_context": MCPModelConfig(
                model_name="gemini-1.5-flash",
                provider="google",
                temperature=0.7,
                max_tokens=4096
            )
        }
        
        # Initialize models
        self._init_models()
        
    def _init_models(self):
        """Initialize all required models."""
        try:
            # Initialize Gemini
            google_api_key = os.getenv("GOOGLE_API_KEY")
            if not google_api_key:
                raise ValueError("GOOGLE_API_KEY not found in environment variables")
                
            genai.configure(api_key=google_api_key)
            self.gemini = genai.GenerativeModel('gemini-pro')
            
            # Initialize DSPy with Postgres for RAG
            try:
                self.lm = dspy.Google(
                    model="gemini-1.5-flash",
                    api_key=google_api_key,
                    max_output_tokens=2048
                )
                dspy.settings.configure(lm=self.lm)
                logger.info("DSPy configured with Google model")
                
                # Initialize Postgres RAG retriever
                self.retriever = PgVectorRM(
                    database_url=os.getenv("DATABASE_URL"),
                    table_name="document_chunks",
                    embedding_field="embedding",
                    k=3
                )
                
            except Exception as e:
                logger.error(f"Failed to initialize DSPy or Postgres retriever: {e}")
                self.lm = None
                self.retriever = None
            
            # Initialize Whisper with local model cache
            whisper_cache_dir = os.path.join(CACHE_DIR, "whisper")
            os.makedirs(whisper_cache_dir, exist_ok=True)
            
            try:
                self.whisper = WhisperModel(
                    model_size_or_path="small",
                    device="cpu",
                    compute_type="int8",
                    download_root=whisper_cache_dir
                )
                logger.info(f"Whisper model initialized and cached at {whisper_cache_dir}")
            except Exception as e:
                logger.error(f"Failed to initialize Whisper model: {e}")
                logger.warning("Audio transcription will not be available")
                self.whisper = None
            
            logger.info("All models initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize models: {e}")
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def process(self, model_key: str, input_data: Dict[str, Any]) -> Any:
        """Process input using the specified model with support for RAG."""
        if model_key not in self.models:
            raise ValueError(f"Unknown model key: {model_key}")
            
        if model_key == "chat_with_context":
            return await self._process_chat_with_context(input_data)
            
        # Existing processing logic for other model keys
        model_config = self.models[model_key]
        if model_config.provider == "google":
            return await self._process_with_gemini(model_config, input_data)
        elif model_config.provider == "whisper":
            return await self._process_with_whisper(model_config, input_data)
        else:
            raise ValueError(f"Unsupported provider: {model_config.provider}")

        config = self.models[model_key]
        handler_name = f"_handle_{model_key}"
        handler = getattr(self, handler_name, None)
        
        if not handler:
            raise ValueError(f"No handler for model key: {model_key}")

        try:
            logger.info(f"Processing request with {model_key}")
            result = await handler(input_data, config)
            return result
        except Exception as e:
            logger.error(f"Error in {model_key} handler: {e}")
            raise

    # --- Model Handlers ---
    
    async def _handle_concept_extractor(
        self,
        text: str,
        language: str = "English",
        **kwargs
    ) -> List[Dict]:
        """Extract key concepts from text using DSPy and RAG with Postgres."""
        class ConceptExtractor(dspy.Signature):
            """Extract key concepts from educational content with source locations."""
            context = dspy.InputField(desc="The text to analyze")
            language = dspy.InputField(desc="Language of the text", default="English")
            concepts = dspy.OutputField(desc="JSON array of concepts with explanations and source locations")
        
        try:
            # First, retrieve relevant context using RAG
            retrieved = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.retriever(text, k=3) if self.retriever else []
            )
            
            # Combine retrieved context with the input text
            context = "\n\n".join([doc.get_text() for doc in retrieved] + [text])
            
            # Generate concepts using DSPy
            predictor = dspy.Predict(ConceptExtractor)
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: predictor(context=context, language=language)
            )
            
            # Parse the result
            concepts = json.loads(result.concepts)
            if not isinstance(concepts, list):
                concepts = [concepts]
                
            return concepts
            
        except Exception as e:
            logger.error(f"Error in concept extraction: {e}")
            return [
                {
                    "concept": "Example Concept",
                    "explanation": "This is a fallback concept extraction. The actual extraction failed.",
                    "source_location": "N/A"
                }
            ]

    async def _handle_explanation_generator(self, input_data: Dict, config: MCPModelConfig) -> str:
        """Generate explanation for a concept using DSPy and RAG with Postgres."""
        class ConceptExplainer(dspy.Signature):
            """Generate an explanation for a concept with context from retrieved documents."""
            concept = dspy.InputField(desc="The concept to explain")
            context = dspy.InputField(desc="Relevant context from documents")
            comprehension_level = dspy.InputField(desc="Target comprehension level", default="beginner")
            language = dspy.InputField(desc="Language for the explanation", default="English")
            explanation = dspy.OutputField(desc="Clear and concise explanation of the concept")
        
        try:
            if not self.lm or not self.retriever:
                raise ValueError("DSPy or Postgres retriever not initialized")
            
            # Retrieve relevant context using RAG
            retrieved = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.retriever(
                    input_data.get('concept', '') + " " + input_data.get('context', ''), 
                    k=3
                )
            )
            
            # Combine retrieved context with the input context
            context = "\n\n".join([doc.get_text() for doc in retrieved] + 
                                  [input_data.get('context', '')])
            
            # Generate explanation using DSPy
            predictor = dspy.Predict(ConceptExplainer)
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: predictor(
                    concept=input_data.get('concept', 'the topic'),
                    context=context,
                    comprehension_level=input_data.get('comprehension_level', 'beginner'),
                    language=input_data.get('language', 'English')
                )
            )
            
            return result.explanation
            
        except Exception as e:
            logger.error(f"Error generating explanation: {e}")
            return "I apologize, but I couldn't generate an explanation at the moment. Please try again later."

    async def _handle_quiz_generator(self, input_data: Dict, config: MCPModelConfig) -> Dict:
        """Generate quiz questions using DSPy and RAG with Postgres."""
        class QuizGenerator(dspy.Signature):
            """Generate quiz questions based on the provided context and parameters."""
            context = dspy.InputField(desc="Relevant context for generating quiz questions")
            difficulty = dspy.InputField(desc="Difficulty level of the quiz", default="medium")
            num_questions = dspy.InputField(desc="Number of questions to generate", default=5)
            quiz_json = dspy.OutputField(desc="JSON string with quiz questions, options, correct answers, and explanations")
        
        try:
            if not self.lm or not self.retriever:
                raise ValueError("DSPy or Postgres retriever not initialized")
            
            # Retrieve relevant context using RAG
            retrieved = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.retriever(
                    input_data.get('content', ''),
                    k=3
                )
            )
            
            # Combine retrieved context with the input content
            context = "\n\n".join([doc.get_text() for doc in retrieved] + 
                                  [input_data.get('content', '')])
            
            # Generate quiz using DSPy
            predictor = dspy.Predict(QuizGenerator)
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: predictor(
                    context=context,
                    difficulty=input_data.get('difficulty', 'medium'),
                    num_questions=input_data.get('num_questions', 5)
                )
            )
            
            # Parse the result
            try:
                quiz_data = json.loads(result.quiz_json)
                if not isinstance(quiz_data, dict) or 'questions' not in quiz_data:
                    raise ValueError("Invalid quiz format")
                return quiz_data
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Failed to parse quiz response: {e}")
                return {
                    "questions": [
                        {
                            "question": "Example question (parsing failed)",
                            "options": ["Option 1", "Option 2", "Option 3", "Option 4"],
                            "correct_answer": "Option 1",
                            "explanation": "This is a fallback question. The actual quiz generation failed to parse."
                        }
                    ]
                }
            
        except Exception as e:
            logger.error(f"Error generating quiz: {e}")
            return {"questions": []}

    async def _handle_flashcard_generator(self, input_data: Dict, config: MCPModelConfig) -> List[Dict]:
        """Generate flashcards using DSPy and RAG with Postgres."""
        class FlashcardGenerator(dspy.Signature):
            """Generate flashcards based on the provided concepts and context."""
            concepts = dspy.InputField(desc="List of concepts to create flashcards for")
            context = dspy.InputField(desc="Relevant context for the concepts")
            flashcards_json = dspy.OutputField(desc="JSON array of flashcards with 'question' and 'answer' fields")
        
        try:
            if not self.lm or not self.retriever:
                raise ValueError("DSPy or Postgres retriever not initialized")
            
            concepts = input_data.get('concepts', [])
            if not concepts:
                logger.warning("No concepts provided for flashcard generation")
                return []
            
            # Retrieve relevant context using RAG for all concepts
            concept_text = ", ".join(concepts) if isinstance(concepts, list) else str(concepts)
            retrieved = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.retriever(concept_text, k=3)
            )
            
            # Combine retrieved context
            context = "\n\n".join([doc.get_text() for doc in retrieved])
            
            # Generate flashcards using DSPy
            predictor = dspy.Predict(FlashcardGenerator)
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: predictor(
                    concepts=concepts,
                    context=context
                )
            )
            
            # Parse the result
            try:
                flashcards = json.loads(result.flashcards_json)
                if not isinstance(flashcards, list):
                    raise ValueError("Invalid flashcard format")
                return flashcards
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Failed to parse flashcard response: {e}")
                return [
                    {
                        "question": "Example Question (parsing failed)",
                        "answer": "This is a fallback flashcard. The actual flashcard generation failed to parse."
                    }
                ]
            
        except Exception as e:
            logger.error(f"Error generating flashcards: {e}")
            return [
                {
                    "question": "Example Question",
                    "answer": "This is a fallback flashcard. The actual flashcard generation failed."
                }
            ]

    async def _handle_speech_to_text(self, input_data: Dict, config: MCPModelConfig) -> List[Dict]:
        """Transcribe audio to text with timestamps using Whisper."""
        try:
            if not hasattr(self, 'whisper') or self.whisper is None:
                logger.error("Whisper model not initialized")
                return [{"text": "Speech-to-text is not available.", "start": 0, "end": 0}]
                
            audio_path = input_data.get("audio_path")
            if not audio_path or not os.path.exists(audio_path):
                logger.error(f"Audio file not found: {audio_path}")
                return [{"text": "Audio file not found.", "start": 0, "end": 0}]
            
            # Transcribe the audio file
            segments, _ = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.whisper.transcribe(
                    audio_path,
                    language=input_data.get("language"),
                    beam_size=5,
                    vad_filter=True
                )
            )
            
            # Convert segments to the expected format
            return [
                {
                    "text": segment.text,
                    "start": segment.start,
                    "end": segment.end
                }
                for segment in segments
            ]
            
        except Exception as e:
            logger.error(f"Error in speech to text: {e}")
            return [{"text": f"Error transcribing audio: {str(e)}", "start": 0, "end": 0}]
            
    def _parse_concepts(self, raw_text: str) -> List[Dict]:
        """Parse concepts from LLM response."""
        try:
            # Clean the response
            json_str = raw_text.strip()
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0].strip()
                
            concepts = json.loads(json_str)
            
            # Standardize format
            standardized = []
            for concept in concepts:
                std_concept = {
                    "title": concept.get("concept", concept.get("title", "Untitled Concept")),
                    "explanation": concept.get("explanation", concept.get("description", "")),
                    "source_page": concept.get("source_page_number"),
                    "start_time": concept.get("source_video_timestamp_start_seconds"),
                    "end_time": concept.get("source_video_timestamp_end_seconds")
                }
                standardized.append(std_concept)
                
            return standardized
            
        except Exception as e:
            logger.error(f"Error parsing concepts: {e}")
            raise

    def _format_quiz_prompt(self, input_data: Dict) -> str:
        """Format prompt for quiz generation."""
        num_questions = input_data.get("num_questions", 5)
        text = input_data.get("text", "")
        concepts = input_data.get("concepts", [])
        
        if concepts:
            text = "\n".join(f"- {c.get('title')}: {c.get('explanation')}" for c in concepts)
            
        return f"""Generate {num_questions} quiz questions based on the following content.
For each question, provide:
1. The question
2. 4 possible answers (A-D)
3. The correct answer
4. A brief explanation

Content:
{text}

Format your response as a JSON array of question objects."""

    def _parse_quiz(self, raw_text: str) -> Dict:
        """Parse quiz questions from LLM response."""
        try:
            # Clean the response
            json_str = raw_text.strip()
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0].strip()
                
            questions = json.loads(json_str)
            return {"questions": questions}
            
        except Exception as e:
            logger.error(f"Error parsing quiz: {e}")
            raise

    def _format_flashcard_prompt(self, concepts: List[Dict]) -> str:
        """Format prompt for flashcard generation."""
        return f"""Create concise flashcards for the following concepts.
For each concept, create a flashcard with:
- Front: The concept/topic
- Back: A clear, concise explanation

Concepts:
{json.dumps(concepts, indent=2)}

Format your response as a JSON array of flashcard objects with 'front' and 'back' keys."""

    def _parse_flashcards(self, raw_text: str) -> List[Dict]:
        """Parse flashcards from LLM response."""
        try:
            # Clean the response
            json_str = raw_text.strip()
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0].strip()
                
            return json.loads(json_str)
            
        except Exception as e:
            logger.error(f"Error parsing flashcards: {e}")
            raise

    async def _process_chat_with_context(self, context: Dict[str, Any]) -> str:
        """Process chat with RAG context."""
        try:
            message = context.get("message", "")
            history = context.get("history", [])
            context_chunks = context.get("context_chunks", [])
            language = context.get("language", "English")
            comprehension_level = context.get("comprehension_level", "beginner")
            
            # Prepare context for the model
            context_prompt = self._build_context_prompt(
                message=message,
                context_chunks=context_chunks,
                language=language,
                comprehension_level=comprehension_level
            )
            
            # Generate response using the model
            response = await self._generate_response(
                model_config=self.models["chat_with_context"],
                prompt=context_prompt,
                history=history
            )
            
            return response
            
        except Exception as e:
            logger.error(f"Error in chat with context: {e}", exc_info=True)
            raise
    
    def _build_context_prompt(
        self,
        message: str,
        context_chunks: List[Dict[str, Any]],
        language: str = "English",
        comprehension_level: str = "beginner"
    ) -> str:
        """Build a prompt with RAG context."""
        # Start with system message
        prompt_parts = [
            "You are a helpful AI assistant that answers questions based on the provided context.",
            f"Language: {language}",
            f"Comprehension Level: {comprehension_level}",
            "\n### Context:\n"
        ]
        
        # Add context chunks
        for i, chunk in enumerate(context_chunks, 1):
            source = chunk.get("source", f"Document {i}")
            content = chunk.get("content", "")
            prompt_parts.append(f"[{source}]\n{content}\n")
        
        # Add user message
        prompt_parts.extend([
            "\n### Question:",
            message,
            "\n### Answer:"
        ])
        
        return "\n".join(prompt_parts)
    
    async def _generate_response(
        self,
        model_config: MCPModelConfig,
        prompt: str,
        history: List[Dict[str, str]] = None
    ) -> str:
        """Generate a response using the specified model."""
        try:
            if model_config.provider == "google":
                # If we have a language model configured, use it
                if self.lm:
                    # Prepare chat history
                    chat = self.gemini.start_chat(history=[])
                    
                    # Add previous messages to history if available
                    if history:
                        for msg in history:
                            role = "user" if msg.get("role") == "user" else "model"
                            chat.send_message(msg.get("content", ""), role=role)
                    
                    # Generate response
                    response = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: chat.send_message(
                            prompt,
                            generation_config={
                                "temperature": model_config.temperature,
                                "max_output_tokens": model_config.max_tokens
                            }
                        )
                    )
                    
                    return response.text
                else:
                    # Fall back to direct API call
                    model = genai.GenerativeModel('gemini-pro')
                    response = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: model.generate_content(
                            prompt,
                            generation_config={
                                "temperature": model_config.temperature,
                                "max_output_tokens": model_config.max_tokens
                            }
                        )
                    )
                    return response.text
                    
            else:
                raise ValueError(f"Unsupported provider: {model_config.provider}")
                
        except Exception as e:
            logger.error(f"Error generating response: {e}", exc_info=True)
            raise
    
    async def stream_chat_with_context(self, context: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """Stream chat response with RAG context."""
        try:
            # Generate the full response first (for simplicity, can be optimized)
            response = await self._process_chat_with_context(context)
            
            # Stream the response in chunks
            for i in range(0, len(response), 50):
                yield response[i:i+50]
                await asyncio.sleep(0.02)  # Simulate streaming
                
        except Exception as e:
            logger.error(f"Error in streaming chat: {e}", exc_info=True)
            yield "I'm sorry, I encountered an error processing your request."

# Singleton instance
mcp_service = MCPService()