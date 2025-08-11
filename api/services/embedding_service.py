"""
Embedding Service

This module provides embedding functionality using various providers (Mistral, Google Gemini, etc.)
with fallback mechanisms and batching support.
"""
import os
import logging
from typing import List, Optional, Dict, Any
from mistralai.client import MistralClient
import google.generativeai as genai
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

class EmbeddingService:
    """Service for generating and managing text embeddings."""
    
    def __init__(self):
        """Initialize the embedding service with available providers."""
        self.providers = self._initialize_providers()
        self.active_provider = self._select_primary_provider()
        logger.info(f"Initialized EmbeddingService with primary provider: {self.active_provider}")
    
    def _initialize_providers(self) -> Dict[str, Any]:
        """Initialize available embedding providers."""
        providers = {}
        
        # Initialize Mistral
        mistral_key = os.getenv("MISTRAL_API_KEY")
        if mistral_key:
            try:
                providers["mistral"] = {
                    'client': MistralClient(api_key=mistral_key),
                    'model': 'mistral-embed',
                    'batch_size': 10
                }
                logger.info("Initialized Mistral embedding provider")
            except Exception as e:
                logger.error(f"Failed to initialize Mistral client: {e}")
        
        # Initialize Google Gemini
        google_api_key = os.getenv("GOOGLE_API_KEY")
        if google_api_key:
            try:
                genai.configure(api_key=google_api_key)
                providers["google"] = {
                    'client': genai,
                    'model': 'models/embedding-001',  # Update with actual model name
                    'batch_size': 20
                }
                logger.info("Initialized Google Gemini embedding provider")
            except Exception as e:
                logger.error(f"Failed to initialize Google Gemini client: {e}")
        
        return providers
    
    def _select_primary_provider(self) -> Optional[str]:
        """Select the primary embedding provider based on availability."""
        preferred_order = ["mistral", "google"]  # Order of preference
        for provider in preferred_order:
            if provider in self.providers:
                return provider
        return None
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def get_embeddings(
        self, 
        texts: List[str], 
        provider: Optional[str] = None
    ) -> List[List[float]]:
        """
        Generate embeddings for a list of texts using the specified provider.
        
        Args:
            texts: List of text strings to embed
            provider: Optional provider name to force (mistral, google)
            
        Returns:
            List of embedding vectors (one per input text)
            
        Raises:
            ValueError: If no embedding provider is available
        """
        if not texts:
            return []
            
        provider_name = provider or self.active_provider
        if not provider_name or provider_name not in self.providers:
            raise ValueError("No valid embedding provider available")
            
        provider_info = self.providers[provider_name]
        
        try:
            if provider_name == "mistral":
                return await self._get_mistral_embeddings(texts, provider_info)
            elif provider_name == "google":
                return await self._get_google_embeddings(texts, provider_info)
            else:
                raise ValueError(f"Unsupported provider: {provider_name}")
                
        except Exception as e:
            logger.error(f"Error with {provider_name} embeddings: {e}")
            if provider != self.active_provider:
                # If we were already trying a fallback, re-raise
                raise
                
            # Try fallback providers
            for fallback in [p for p in self.providers if p != self.active_provider]:
                try:
                    logger.info(f"Trying fallback provider: {fallback}")
                    return await self.get_embeddings(texts, provider=fallback)
                except Exception as fallback_error:
                    logger.error(f"Fallback provider {fallback} failed: {fallback_error}")
                    continue
                    
            # If we get here, all providers failed
            raise ValueError("All embedding providers failed") from e
    
    async def _get_mistral_embeddings(
        self, 
        texts: List[str], 
        provider_info: Dict[str, Any]
    ) -> List[List[float]]:
        """Get embeddings using Mistral's API."""
        client = provider_info['client']
        batch_size = provider_info['batch_size']
        embeddings = []
        
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            response = client.embeddings(
                model=provider_info['model'],
                input=batch
            )
            batch_embeddings = [item.embedding for item in response.data]
            embeddings.extend(batch_embeddings)
            
        return embeddings
    
    async def _get_google_embeddings(
        self, 
        texts: List[str], 
        provider_info: Dict[str, Any]
    ) -> List[List[float]]:
        """Get embeddings using Google's API."""
        client = provider_info['client']
        batch_size = provider_info['batch_size']
        embeddings = []
        
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            response = client.embed_content(
                model=provider_info['model'],
                content={"parts": [{"text": text} for text in batch]},
                task_type="retrieval_document"
            )
            batch_embeddings = [item['embedding'] for item in response.get('embeddings', [])]
            embeddings.extend(batch_embeddings)
            
        return embeddings
    
    async def get_embedding(
        self, 
        text: str, 
        provider: Optional[str] = None
    ) -> List[float]:
        """
        Get a single embedding vector for the input text.
        
        Args:
            text: Input text to embed
            provider: Optional provider name to force
            
        Returns:
            Embedding vector as a list of floats
        """
        if not text:
            return []
            
        results = await self.get_embeddings([text], provider)
        return results[0] if results else []


# Singleton instance
embedding_service = EmbeddingService()
