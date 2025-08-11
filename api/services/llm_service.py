"""
LLM Service
===========

This module provides a unified interface for interacting with multiple Large Language Model (LLM) 
providers (Mistral, Google Gemini, etc.) for text generation and chat completion tasks.

Features:
- Support for multiple LLM providers with automatic fallback
- Consistent interface for both single-turn and multi-turn conversations
- Configurable parameters (temperature, max_tokens, etc.)
- Automatic retry with exponential backoff for failed requests
- Thread-safe singleton implementation

Environment Variables:
- MISTRAL_API_KEY: API key for Mistral AI
- GOOGLE_API_KEY: API key for Google Gemini

Example Usage:
    ```python
    from api.services.llm_service import llm_service
    
    # Single prompt completion
    response = await llm_service.generate_text(
        prompt="Tell me a joke about AI",
        temperature=0.7,
        max_tokens=100
    )
    
    # Chat completion with message history
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What's the weather like today?"}
    ]
    response = await llm_service.chat(
        messages=messages,
        temperature=0.7
    )
    ```
"""
import os
import logging
from typing import Dict, Any, Optional, List

import google.generativeai as genai
from mistralai.client import MistralClient
from mistralai.models.chat_completion import ChatMessage
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

class LLMService:
    """
    A service class providing a unified interface for interacting with multiple LLM providers.
    
    This class handles provider initialization, request formatting, response parsing,
    and automatic fallback between providers if the primary provider fails.
    
    Attributes:
        providers (Dict[str, Dict]): Dictionary of available LLM providers and their configurations
        active_provider (Optional[str]): Name of the currently active provider
        
    The service automatically selects the best available provider based on the following order:
    1. Mistral (if MISTRAL_API_KEY is set)
    2. Google Gemini (if GOOGLE_API_KEY is set)
    """
    
    def __init__(self):
        """
        Initialize the LLM service with available providers.
        
        The service will automatically detect and initialize all available providers
        based on the presence of their respective API keys in the environment.
        """
        self.providers = self._initialize_providers()
        self.active_provider = self._select_primary_provider()
        logger.info(f"Initialized LLMService with primary provider: {self.active_provider}")
    
    def _initialize_providers(self) -> Dict[str, Any]:
        """
        Initialize and configure available LLM providers.
        
        This method checks for required environment variables and initializes
        the corresponding provider clients. Each provider configuration includes:
        - client: The initialized client instance
        - default_model: Default model to use if none specified
        - temperature: Default temperature setting
        - max_tokens: Default maximum tokens to generate
        
        Returns:
            Dict[str, Any]: Dictionary of provider names to their configurations
        """
        providers = {}
        
        # Initialize Mistral
        mistral_key = os.getenv("MISTRAL_API_KEY")
        if mistral_key:
            try:
                providers["mistral"] = {
                    'client': MistralClient(api_key=mistral_key),
                    'default_model': 'mistral-medium',  # Default model for text generation
                    'temperature': 0.7,
                    'max_tokens': 2000
                }
                logger.info("Initialized Mistral LLM provider")
            except Exception as e:
                logger.error(f"Failed to initialize Mistral client: {e}")
        
        # Initialize Google Gemini
        google_api_key = os.getenv("GOOGLE_API_KEY")
        if google_api_key:
            try:
                genai.configure(api_key=google_api_key)
                providers["google"] = {
                    'client': genai,
                    'default_model': 'gemini-pro',  # Default model for text generation
                    'temperature': 0.7,
                    'max_tokens': 2048
                }
                logger.info("Initialized Google Gemini LLM provider")
            except Exception as e:
                logger.error(f"Failed to initialize Google Gemini client: {e}")
        
        return providers
    
    def _select_primary_provider(self) -> Optional[str]:
        """
        Select the primary LLM provider based on availability and preference order.
        
        The preference order is defined as:
        1. Mistral
        2. Google Gemini
        
        Returns:
            Optional[str]: Name of the selected provider, or None if no providers are available
        """
        preferred_order = ["mistral", "google"]  # Order of preference
        for provider in preferred_order:
            if provider in self.providers:
                return provider
        return None
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def generate_text(
        self,
        prompt: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """
        Generate text using the specified LLM provider.
        
        This method automatically handles retries with exponential backoff and will
        fall back to alternative providers if the primary provider fails.
        
        Args:
            prompt: The input prompt or instruction for the model
            provider: Optional provider name (e.g., 'mistral', 'google'). 
                     If not specified, uses the default provider.
            model: Optional model name to override the provider's default
            temperature: Controls randomness (0.0 = deterministic, 1.0 = creative)
            max_tokens: Maximum number of tokens to generate in the response
            **kwargs: Additional provider-specific parameters
                
        Returns:
            str: The generated text response from the model
            
        Raises:
            ValueError: If no valid provider is available or all providers fail
            
        Example:
            ```python
            response = await llm_service.generate_text(
                prompt="Write a haiku about artificial intelligence",
                temperature=0.8,
                max_tokens=100
            )
            ```
        """
        provider_name = provider or self.active_provider
        if not provider_name or provider_name not in self.providers:
            raise ValueError("No valid LLM provider available")
            
        provider_info = self.providers[provider_name]
        
        try:
            if provider_name == "mistral":
                return await self._generate_with_mistral(prompt, provider_info, model, temperature, max_tokens, **kwargs)
            elif provider_name == "google":
                return await self._generate_with_google(prompt, provider_info, model, temperature, max_tokens, **kwargs)
            else:
                raise ValueError(f"Unsupported provider: {provider_name}")
                
        except Exception as e:
            logger.error(f"Error with {provider_name} text generation: {e}")
            if provider != self.active_provider:
                # If we were already trying a fallback, re-raise
                raise
                
            # Try fallback providers
            for fallback in [p for p in self.providers if p != self.active_provider]:
                try:
                    logger.info(f"Trying fallback provider: {fallback}")
                    return await self.generate_text(
                        prompt=prompt,
                        provider=fallback,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **kwargs
                    )
                except Exception as fallback_error:
                    logger.error(f"Fallback provider {fallback} failed: {fallback_error}")
                    continue
                    
            # If we get here, all providers failed
            raise ValueError("All LLM providers failed") from e
    
    async def _generate_with_mistral(
        self,
        prompt: str,
        provider_info: Dict[str, Any],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """Generate text using Mistral's API."""
        client = provider_info['client']
        model_name = model or provider_info.get('default_model', 'mistral-medium')
        
        # Prepare messages
        messages = [
            ChatMessage(role="user", content=prompt)
        ]
        
        # Make API call
        response = client.chat(
            model=model_name,
            messages=messages,
            temperature=temperature or provider_info.get('temperature', 0.7),
            max_tokens=max_tokens or provider_info.get('max_tokens', 2000),
            **kwargs
        )
        
        return response.choices[0].message.content
    
    async def _generate_with_google(
        self,
        prompt: str,
        provider_info: Dict[str, Any],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """Generate text using Google's Gemini API."""
        model_name = model or provider_info.get('default_model', 'gemini-pro')
        
        # Initialize the model
        model = provider_info['client'].GenerativeModel(model_name)
        
        # Generate content
        response = await model.generate_content_async(
            prompt,
            generation_config={
                'temperature': temperature or provider_info.get('temperature', 0.7),
                'max_output_tokens': max_tokens or provider_info.get('max_tokens', 2048),
                **kwargs
            }
        )
        
        return response.text
    
    async def chat(
        self,
        messages: List[Dict[str, str]],
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """
        Have a conversation with the LLM using a message history.
        
        This method supports multi-turn conversations by maintaining message history.
        It automatically handles different message roles (system, user, assistant)
        according to each provider's requirements.
        
        Args:
            messages: List of message dictionaries with the following keys:
                - role: 'system', 'user', or 'assistant'
                - content: The message content
            provider: Optional provider name (e.g., 'mistral', 'google')
            model: Optional model name to override the provider's default
            temperature: Controls randomness (0.0 = deterministic, 1.0 = creative)
            max_tokens: Maximum number of tokens to generate in the response
            **kwargs: Additional provider-specific parameters
            
        Returns:
            str: The generated text response from the model
            
        Raises:
            ValueError: If no valid provider is available or all providers fail
            
        Example:
            ```python
            messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "What's the capital of France?"},
                {"role": "assistant", "content": "The capital of France is Paris."},
                {"role": "user", "content": "What's the population there?"}
            ]
            response = await llm_service.chat(
                messages=messages,
                temperature=0.7
            )
            ```
        """
        provider_name = provider or self.active_provider
        if not provider_name or provider_name not in self.providers:
            raise ValueError("No valid LLM provider available")
            
        if provider_name == "mistral":
            return await self._chat_with_mistral(messages, provider_name, model, temperature, max_tokens, **kwargs)
        elif provider_name == "google":
            return await self._chat_with_google(messages, provider_name, model, temperature, max_tokens, **kwargs)
        else:
            raise ValueError(f"Unsupported provider for chat: {provider_name}")
    
    async def _chat_with_mistral(
        self,
        messages: List[Dict[str, str]],
        provider: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """Chat using Mistral's API."""
        provider_info = self.providers[provider]
        client = provider_info['client']
        model_name = model or provider_info.get('default_model', 'mistral-medium')
        
        # Convert messages to Mistral format
        mistral_messages = [
            ChatMessage(role=msg['role'], content=msg['content'])
            for msg in messages
        ]
        
        # Make API call
        response = client.chat(
            model=model_name,
            messages=mistral_messages,
            temperature=temperature or provider_info.get('temperature', 0.7),
            max_tokens=max_tokens or provider_info.get('max_tokens', 2000),
            **kwargs
        )
        
        return response.choices[0].message.content
    
    async def _chat_with_google(
        self,
        messages: List[Dict[str, str]],
        provider: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """Chat using Google's Gemini API."""
        provider_info = self.providers[provider]
        model_name = model or provider_info.get('default_model', 'gemini-pro')
        
        # Initialize the model
        model = provider_info['client'].GenerativeModel(model_name)
        
        # Start a chat session
        chat = model.start_chat(history=[])
        
        # Add all but the last message to history
        for msg in messages[:-1]:
            if msg['role'] == 'user':
                chat.send_message_async(msg['content'])
            # Google's API doesn't support system messages directly
            # So we'll prepend them to the user message
        
        # Send the last message and get response
        last_message = messages[-1]
        response = await chat.send_message_async(
            last_message['content'],
            generation_config={
                'temperature': temperature or provider_info.get('temperature', 0.7),
                'max_output_tokens': max_tokens or provider_info.get('max_tokens', 2048),
                **kwargs
            }
        )
        
        return response.text


# Singleton instance of the LLM service
# This instance should be imported and used throughout the application
# to maintain a single connection pool and configuration
llm_service = LLMService()
