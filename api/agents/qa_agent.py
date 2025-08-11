"""
Q&A Agent for answering questions about content with source citations.

This module provides the QAAgent class which is responsible for generating
accurate, source-cited answers to user questions by combining information
from multiple sources including the document database and web search results.

Key Features:
- Semantic search for relevant document chunks
- Web search integration for additional context
- Source citation with page numbers and confidence scores
- Follow-up question suggestions
- Configurable search parameters and response generation

Example Usage:
    ```python
    # Initialize the agent
    qa_agent = QAAgent()
    
    # Answer a question about a specific document
    result = await qa_agent.process(
        question="What are the key findings in this document?",
        file_id="doc_123",
        store=db_store
    )
    
    if result["status"] == "success":
        answer = result["result"]
        print(f"Answer: {answer.answer}")
        print(f"Confidence: {answer.confidence:.1%}")
        print("\nSources:")
        for source in answer.sources:
            print(f"- {source['title']} (page {source.get('page', 'N/A')})")
    else:
        print(f"Error: {result['error']}")
    ```
"""
from typing import Dict, Any, List, Optional, Tuple
import json
import logging
from datetime import datetime
from pydantic import BaseModel, Field, validator

from .base_agent import BaseAgent, AgentConfig
from .prompt_loader import PromptLoader
from .dspy_utils import extract_key_concepts
from ..services.llm_service import llm_service
from ..services.embedding_service import embedding_service
from ..services.web_search_service import web_search_service

logger = logging.getLogger(__name__)

class QAConfig(AgentConfig):
    """
    Configuration for the Q&A Agent.
    
    This configuration class controls how the QAAgent generates answers,
    including search parameters, source inclusion, and response formatting.
    
    Attributes:
        max_tokens: Maximum length of generated answers in tokens.
                   Range: 50-4000
                   Default: 1000
        temperature: Controls randomness in response generation.
                    0.0 = deterministic, 1.0 = creative.
                    Range: 0.0-1.0
                    Default: 0.3
        include_sources: Whether to include source citations in answers.
                       Default: True
        max_sources: Maximum number of sources to cite in responses.
                   Range: 1-10
                   Default: 5
        max_web_results: Maximum web search results to include as context.
                       Set to 0 to disable web search.
                       Range: 0-10
                       Default: 3
        similarity_threshold: Minimum relevance score for including content chunks.
                            Higher values make the search more strict.
                            Range: 0.0-1.0
                            Default: 0.7
        use_web_search: Whether to augment answers with web search results.
                       Can be overridden per query.
                       Default: True
        max_chunks: Maximum number of content chunks to include in context.
                   More chunks provide more context but increase token usage.
                   Range: 1-50
                   Default: 10
    """
    max_tokens: int = Field(
        default=1000,
        description="Maximum number of tokens for the answer",
        ge=50,
        le=4000,
        example=1000
    )
    temperature: float = Field(
        default=0.3,
        description="Temperature for response generation (0.0-1.0)",
        ge=0.0,
        le=1.0,
        example=0.3
    )
    include_sources: bool = Field(
        default=True,
        description="Whether to include source citations in the answer",
        example=True
    )
    max_sources: int = Field(
        default=5,
        description="Maximum number of sources to include (1-10)",
        ge=1,
        le=10,
        example=5
    )
    max_web_results: int = Field(
        default=3,
        description="Maximum web search results to include (0-10)",
        ge=0,
        le=10,
        example=3
    )
    similarity_threshold: float = Field(
        default=0.7,
        description="Minimum similarity score for including a chunk (0.0-1.0)",
        ge=0.0,
        le=1.0,
        example=0.7
    )
    use_web_search: bool = Field(
        default=True,
        description="Whether to use web search for additional context",
        example=True
    )
    max_chunks: int = Field(
        default=10,
        description="Maximum number of chunks to include in context (1-50)",
        ge=1,
        le=50,
        example=10
    )
    
    @validator('temperature')
    def validate_temperature(cls, v):
        if v < 0.0 or v > 1.0:
            raise ValueError('Temperature must be between 0.0 and 1.0')
        return v

class QAResult(BaseModel):
    """
    Model representing a Q&A response with metadata and sources.
    
    This model structures the response from the QAAgent, including the generated
    answer, confidence score, source citations, and related information.
    
    Attributes:
        answer: The generated answer to the question, formatted in Markdown.
        confidence: Confidence score of the answer (0.0-1.0).
        sources: List of source documents or web pages used to generate the answer.
               Each source includes metadata like title, URL, and page number.
        suggested_questions: List of follow-up questions that might provide
                          additional relevant information.
        search_queries: The actual search queries used to retrieve information.
        generated_at: ISO 8601 timestamp of when the answer was generated.
    """
    answer: str = Field(
        ...,
        description="The generated answer to the question (Markdown formatted)",
        example="The key findings indicate that regular exercise can reduce the risk of..."
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score of the answer (0.0-1.0)",
        example=0.92
    )
    sources: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of sources used to generate the answer with metadata",
        example=[
            {
                "title": "The Effects of Exercise on Health",
                "url": "https://example.com/exercise-health",
                "page": 42,
                "source_type": "document"
            }
        ]
    )
    suggested_questions: List[str] = Field(
        default_factory=list,
        description="List of follow-up questions suggested by the model",
        example=[
            "What are the specific health benefits mentioned?",
            "Are there any recommended exercise routines?"
        ]
    )
    search_queries: List[str] = Field(
        default_factory=list,
        description="Search queries used to find relevant information",
        example=["effects of exercise on health benefits"]
    )
    generated_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(),
        description="ISO 8601 timestamp when the answer was generated",
        example="2025-08-01T23:25:57.123456"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "answer": "Paris is the capital of France.",
                "confidence": 0.95,
                "sources": [
                    {
                        "text": "Paris is the capital and most populous city of France.",
                        "page": 1,
                        "score": 0.92,
                        "source_type": "document"
                    }
                ],
                "suggested_questions": [
                    "What is the population of Paris?",
                    "What are some famous landmarks in Paris?"
                ],
                "search_queries": ["capital of France"],
                "generated_at": "2023-01-01T12:00:00Z"
            }
        }

class QAAgent(BaseAgent[QAConfig]):
    """
    Agent for answering questions with source citations and context awareness.
    
    The QAAgent provides accurate, well-sourced answers to user questions by:
    1. Performing semantic search across document chunks
    2. Augmenting with web search results when beneficial
    3. Generating clear, well-structured responses with proper citations
    4. Suggesting relevant follow-up questions
    
    Key Features:
    - Semantic understanding of questions and content
    - Multi-source evidence gathering
    - Confidence scoring for answers
    - Source citation with page numbers
    - Follow-up question generation
    - Configurable search and generation parameters
    
    The agent follows a multi-step process to ensure high-quality answers:
    1. Question analysis and query generation
    2. Content retrieval from multiple sources
    3. Context preparation and evidence synthesis
    4. Answer generation with proper attribution
    5. Confidence scoring and source citation
    
    Example:
        ```python
        # Initialize with custom configuration
        config = QAConfig(
            max_tokens=1500,
            temperature=0.2,
            max_sources=3,
            use_web_search=True
        )
        qa_agent = QAAgent(config)
        
        # Answer a question with context
        result = await qa_agent.process(
            question="What are the main benefits of the proposed solution?",
            file_id="doc_456",
            store=db_store,
            context={"user_id": "user_123"}
        )
        
        # Process the result
        if result["status"] == "success":
            answer = result["result"]
            print(f"Answer: {answer.answer}")
            print(f"Confidence: {answer.confidence:.1%}")
            
            if answer.sources:
                print("\nSources:")
                for i, source in enumerate(answer.sources, 1):
                    print(f"{i}. {source.get('title', 'Untitled')}")
                    if 'url' in source:
                        print(f"   URL: {source['url']}")
                    if 'page' in source:
                        print(f"   Page: {source['page']}")
        ```
    """
    
    @classmethod
    def get_default_config(cls) -> QAConfig:
        """Return the default configuration for this agent."""
        return QAConfig()
    
    async def process(
        self, 
        question: str,
        file_id: Optional[str] = None,
        store: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
        use_web_search: Optional[bool] = None,
        chat_history: Optional[str] = None,
        user_id: Optional[str] = None,
        history_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Answer a question using content from the database and optionally the web.
        
        Args:
            question: The question to answer
            file_id: Optional file ID to search within
            store: Database store instance
            context: Additional context for the question
            use_web_search: Override the default web search setting
            chat_history: Formatted chat history as a string
            user_id: ID of the user making the query
            history_id: ID of the chat history (generated if not provided)
            
        Returns:
            Dictionary containing the answer and related information:
            {
                "status": "success"|"error",
                "result": QAResult if success else None,
                "error": str if error else None,
                "history_id": str  # ID of the chat history
            }
        """
        try:
            if not question:
                raise ValueError("Question cannot be empty")
            
            logger.info(f"Processing question: {question}")
            try:
                # Step 1: Get relevant chunks from the database
                chunks = []
                if file_id and store:
                    chunks = await self._retrieve_relevant_chunks(question, file_id, store)
                
                # Step 2: Get web search results if enabled
                web_results = []
                search_queries = []
                if (use_web_search if use_web_search is not None else self.config.use_web_search):
                    web_results, search_queries = await self._search_web(question)
                
                # Step 3: Prepare context for the LLM
                prepared_context = await self._prepare_context(
                    question=question,
                    chunks=chunks,
                    web_results=web_results,
                    additional_context={
                        **(context or {}),
                        "chat_history": chat_history or "",
                        "user_id": user_id or ""
                    }
                )
                
                # Add search queries to context for the prompt
                if search_queries:
                    prepared_context["search_queries"] = search_queries
                
                # Step 4: Generate answer using LLM
                answer = await self._generate_answer(question, prepared_context)
                
                # Ensure search queries are included in the result
                if search_queries and hasattr(answer, 'search_queries'):
                    answer.search_queries = search_queries
                
                # Convert answer to dict if it's a Pydantic model
                answer_dict = answer.model_dump() if hasattr(answer, 'model_dump') else answer
                
                # Handle chat history if store is provided
                if store and hasattr(store, 'chat_repo'):
                    # Generate history_id if not provided
                    history_id = history_id or str(uuid.uuid4())
                    
                    # Save the interaction to chat history
                    chat_message = {
                        'user_id': user_id,
                        'history_id': history_id,
                        'user_message': question,
                        'assistant_message': answer_dict.get('answer', 'I could not generate a response.'),
                        'context': json.dumps(answer_dict.get('context', {})),
                        'metadata': {
                            'model': 'qa_agent',
                            'language': context.get('language', 'en') if context else 'en',
                            'comprehension_level': context.get('comprehension_level', 'intermediate') if context else 'intermediate',
                            'sources': answer_dict.get('sources', [])
                        }
                    }
                    
                    try:
                        await store.chat_repo.add_chat_message(chat_message)
                    except Exception as e:
                        logger.error(f"Failed to save chat message: {e}")
                    
                    # Include history_id in the response
                    answer_dict['history_id'] = history_id
                
                return {
                    "status": "success",
                    "result": answer_dict,
                    "history_id": history_id if 'history_id' in locals() else None
                }
                
            except Exception as e:
                logger.error(f"Error generating answer: {str(e)}", exc_info=True)
                return {
                    "status": "error",
                    "result": None,
                    "error": str(e),
                    "history_id": None
                }
            
        except Exception as e:
            logger.error(f"Error processing question: {str(e)}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "result": None
            }
    
    async def _retrieve_relevant_chunks(
        self, 
        question: str,
        file_id: str,
        store: Any
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant chunks from the database based on semantic similarity.
        
        Args:
            question: The question to find relevant chunks for
            file_id: ID of the file to search within
            store: Database store instance
            
        Returns:
            List of relevant chunks with metadata
        """
        try:
            logger.info(f"Retrieving relevant chunks for file {file_id}")
            
            # Get the embedding for the question
            question_embedding = await embedding_service.get_embedding(question)
            if not question_embedding:
                logger.warning("Failed to generate question embedding")
                return []
            
            # Query the database for similar chunks
            chunks = await store.chunk_repo.find_similar(
                file_id=file_id,
                embedding=question_embedding,
                limit=self.config.max_chunks,
                min_similarity=self.config.similarity_threshold
            )
            
            logger.info(f"Retrieved {len(chunks)} relevant chunks for question")
            return chunks
            
        except Exception as e:
            logger.error(f"Error retrieving relevant chunks: {e}", exc_info=True)
            return []
    
    async def _search_web(
        self, 
        question: str
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """
        Search the web for additional context.
        
        Args:
            question: The question to search for
            
        Returns:
            Tuple of (search_results, search_queries)
        """
        if not self.config.use_web_search or self.config.max_web_results <= 0:
            return [], []
            
        try:
            # Generate search queries from the question
            search_queries = await self._generate_search_queries(question)
            all_results = []
            
            for query in search_queries[:3]:  # Limit to top 3 queries
                results = await web_search_service.search(
                    query=query,
                    max_results=min(3, self.config.max_web_results)
                )
                all_results.extend(results)
                
                # Don't exceed max results across all queries
                if len(all_results) >= self.config.max_web_results:
                    all_results = all_results[:self.config.max_web_results]
                    break
            
            logger.info(f"Found {len(all_results)} web results for question")
            return all_results, search_queries
            
        except Exception as e:
            logger.error(f"Error searching web: {e}", exc_info=True)
            return [], []
    
    async def _generate_search_queries(self, question: str) -> List[str]:
        """Generate search queries from a question."""
        try:
            prompt = f"""
            Given the following question, generate 1-3 search queries that would help find 
            relevant information to answer it. Return the queries as a JSON array of strings.
            
            Question: {question}
            
            Queries (JSON array):
            """
            
            response = await llm_service.generate_text(
                prompt=prompt,
                temperature=0.3,
                max_tokens=200
            )
            
            # Parse the response as JSON
            try:
                queries = json.loads(response)
                if not isinstance(queries, list):
                    queries = [queries]
                return [str(q).strip() for q in queries if q]
            except json.JSONDecodeError:
                # Fallback: Use the question as the query
                return [question]
                
        except Exception as e:
            logger.error(f"Error generating search queries: {e}")
            return [question]
    
    async def _extract_key_concepts_from_sources(self, sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract key concepts from source content using DSPy.
        
        Args:
            sources: List of source dictionaries with 'content' field
            
        Returns:
            List of extracted key concepts with titles and explanations
        """
        try:
            # Combine content from all sources
            combined_content = "\n\n".join(
                source.get("content", "") 
                for source in sources 
                if source.get("content")
            )
            
            if not combined_content:
                return []
                
            # Extract key concepts using DSPy
            concepts = extract_key_concepts(
                document=combined_content,
                language="english",
                comprehension_level="intermediate"
            )
            
            # Format concepts for the context
            return [
                {
                    "concept_title": concept.get("concept_title", ""),
                    "concept_explanation": concept.get("concept_explanation", ""),
                    "confidence": float(concept.get("confidence", 0.8)),
                    "is_custom": False
                }
                for concept in concepts[:5]  # Limit to top 5 concepts
            ]
            
        except Exception as e:
            logger.warning(f"Failed to extract key concepts from sources: {e}")
            return []
    
    async def _extract_key_concepts_from_question(self, question: str) -> List[Dict[str, Any]]:
        """Extract key concepts from the question using DSPy.
        
        Args:
            question: The input question
            
        Returns:
            List of extracted key concepts
        """
        try:
            if not question.strip():
                return []
                
            # Extract key concepts using DSPy
            concepts = extract_key_concepts(
                document=question,
                language="english",
                comprehension_level="intermediate"
            )
            
            # Format concepts for the context
            return [
                {
                    "concept_title": concept.get("concept_title", ""),
                    "concept_explanation": concept.get("concept_explanation", ""),
                    "confidence": float(concept.get("confidence", 0.8)),
                    "is_custom": False,
                    "source": "question"
                }
                for concept in concepts[:3]  # Limit to top 3 concepts from question
            ]
            
        except Exception as e:
            logger.warning(f"Failed to extract key concepts from question: {e}")
            return []
    
    async def _prepare_context(
        self,
        question: str,
        chunks: List[Dict[str, Any]],
        web_results: List[Dict[str, Any]],
        additional_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Prepare context from multiple sources for the LLM.
        
        Args:
            question: The original question
            chunks: Relevant chunks from the database
            web_results: Results from web search
            additional_context: Any additional context
            
        Returns:
            Dictionary containing the prepared context with key concepts
        """
        context = {
            "question": question,
            "sources": [],
            "additional_context": additional_context,
            "key_concepts": []
        }
        
        # Add document chunks as sources
        for i, chunk in enumerate(chunks[:self.config.max_chunks], 1):
            context["sources"].append({
                "type": "document",
                "content": chunk.get("text", ""),
                "metadata": {
                    "page": chunk.get("page_number"),
                    "chunk_index": i,
                    "score": chunk.get("similarity_score")
                }
            })
        
        # Add web results as sources
        for i, result in enumerate(web_results, 1):
            context["sources"].append({
                "type": "web",
                "title": result.get("title", ""),
                "content": result.get("content", ""),
                "url": result.get("url", ""),
                "metadata": {
                    "source": "web",
                    "relevance_score": result.get("score", 0.0)
                }
            })
        
        # Extract key concepts from sources and question
        if context["sources"]:
            context["key_concepts"].extend(
                await self._extract_key_concepts_from_sources(context["sources"])
            )
        
        # Extract key concepts from the question itself
        context["key_concepts"].extend(
            await self._extract_key_concepts_from_question(question)
        )
        
        # Remove duplicates (same concept_title)
        seen = set()
        unique_concepts = []
        for concept in context["key_concepts"]:
            if concept["concept_title"] not in seen:
                seen.add(concept["concept_title"])
                unique_concepts.append(concept)
        
        context["key_concepts"] = unique_concepts
        
        return context
    
    async def _generate_answer(
        self,
        question: str,
        context: Dict[str, Any]
    ) -> QAResult:
        """
        Generate an answer using the LLM.
        
        Args:
            question: The question to answer
            context: Prepared context with sources
            
        Returns:
            QAResult containing the answer and metadata
        """
        try:
            # Prepare the prompt
            prompt = self._prepare_prompt(question, context)
            
            # Call the LLM
            response = await llm_service.generate_text(
                prompt=prompt,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens
            )
            
            # Parse the response
            return self._parse_llm_response(response, context)
            
        except Exception as e:
            logger.error(f"Error generating answer: {e}", exc_info=True)
            raise ValueError(f"Failed to generate answer: {str(e)}")
    
    def _prepare_prompt(
        self,
        question: str,
        context: Dict[str, Any]
    ) -> str:
        """
        Prepare the prompt for the LLM.
        
        Args:
            question: The question to answer
            context: Prepared context with sources
            
        Returns:
            Formatted prompt string
        """
        return PromptLoader.render_instruction(
            "qa",
            question=question,
            context=context,
            include_sources=self.config.include_sources,
            max_sources=self.config.max_sources,
            current_date=datetime.utcnow().strftime("%Y-%m-%d")
        )
    
    def _parse_llm_response(
        self, 
        response: str,
        context: Dict[str, Any]
    ) -> QAResult:
        """
        Parse and validate the LLM response.
        
        Args:
            response: Raw response from the LLM
            context: Context used for generating the answer
            
        Returns:
            QAResult with parsed data
            
        Raises:
            ValueError: If the response cannot be parsed or is invalid
        """
        try:
            # Try to parse as JSON first
            try:
                data = json.loads(response)
                if not isinstance(data, dict):
                    raise ValueError("Expected JSON object in response")
            except json.JSONDecodeError:
                # If not JSON, treat the entire response as the answer
                data = {"answer": response}
            
            # Ensure required fields are present
            if "answer" not in data:
                raise ValueError("Response is missing 'answer' field")
            
            # Extract sources from context if not provided in response
            if "sources" not in data and "sources" in context:
                data["sources"] = context["sources"]
            
            # Create the result object
            result = QAResult(
                answer=data["answer"],
                confidence=min(max(float(data.get("confidence", 0.8)), 0.0), 1.0),
                sources=data.get("sources", []),
                suggested_questions=data.get("suggested_questions", [])
            )
            
            # Add search queries if present in context
            if "search_queries" in context:
                result.search_queries = context["search_queries"]
            
            return result
            
        except Exception as e:
            logger.error(f"Error parsing LLM response: {e}")
            # Return a basic result with the raw response as the answer
            return QAResult(
                answer=response,
                confidence=0.5,
                sources=context.get("sources", [])
            )
