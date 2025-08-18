"""
DSPy configuration for SynTextAI.

This module handles the initialization and configuration of the DSPy language model
using the application's LLM service.
"""
import logging
from typing import Optional
import dspy
from dspy.teleprompt import BootstrapFewShot

from ..services.llm_service import llm_service

logger = logging.getLogger(__name__)

class DSPyLLM(dspy.LM):
    """DSPy LLM wrapper for SynTextAI's LLM service."""
    
    def __init__(self, model: str = "mistral"):
        super().__init__(model)
        self.model = model
        self.history = []
    
    def basic_request(self, prompt: str, **kwargs):
        # Store the prompt in history for debugging
        self.history.append({"prompt": prompt, "kwargs": kwargs})
        
        # Set better defaults for key concept extraction
        temperature = kwargs.get("temperature", 0.3)  # Lower temperature for more focused outputs
        max_tokens = kwargs.get("max_tokens", 2000)   # More tokens for detailed explanations
        
        # Call the LLM service
        try:
            # Use generate_text method which is the correct method in our LLM service
            response = llm_service.generate_text(
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                provider=kwargs.get("provider", "openai"),  # Default to OpenAI for better JSON handling
                model=kwargs.get("model", "gpt-4")  # Default to GPT-4 for better structured output
            )
            
            # Log the response for debugging
            logger.debug(f"LLM Response: {response[:200]}..." if len(str(response)) > 200 else f"LLM Response: {response}")
            
            # Return the response in the format DSPy expects
            return {
                "choices": [{
                    "message": {"content": response}
                }]
            }
        except Exception as e:
            logger.error(f"Error in DSPy LLM request: {str(e)}")
            raise
    
    def __call__(self, prompt: str = None, **kwargs):
        # If prompt is not in kwargs, check if it's the first positional arg
        if prompt is None and 'prompt' in kwargs:
            prompt = kwargs.pop('prompt')
        elif prompt is None and 'prompt' not in kwargs and len(kwargs) > 0:
            # Try to get the first argument if prompt is not explicitly named
            first_arg = next(iter(kwargs.values()))
            if isinstance(first_arg, str):
                prompt = first_arg
                # Remove the used argument
                kwargs.pop(next(iter(kwargs)))
        
        if prompt is None:
            raise ValueError("No prompt provided")
            
        # Log the prompt and kwargs for debugging
        logger.debug(f"DSPyLLM called with prompt: {prompt[:100]}...")
        logger.debug(f"DSPyLLM kwargs: {kwargs}")
        
        try:
            response = self.basic_request(prompt, **kwargs)
            return [response["choices"][0]["message"]["content"]]
        except Exception as e:
            logger.error(f"Error in DSPyLLM.__call__: {str(e)}")
            logger.exception("DSPyLLM call failed")
            # Return an empty string to prevent crashes, but log the error
            return [""]

def configure_dspy(lm: Optional[dspy.LM] = None):
    """Configure DSPy with the specified language model.
    
    Args:
        lm: Optional pre-initialized DSPy language model. If not provided,
            a default one will be created using the application's LLM service.
    """
    try:
        if lm is None:
            # Initialize with better defaults for key concept extraction
            lm = DSPyLLM(model="gpt-4")
            
            # Configure DSPy to use our LLM with optimized settings
            dspy.settings.configure(
                lm=lm,
                cache=True,  # Enable caching to avoid redundant LLM calls
                temperature=0.3,  # Lower temperature for more deterministic outputs
                max_tokens=2000,  # More tokens for detailed explanations
                max_retries=3,    # Retry failed requests
                timeout=60        # Longer timeout for complex operations
            )
            
            logger.info("DSPy configured with %s (temperature=0.3, max_tokens=2000)", lm.model)
        else:
            dspy.settings.configure(lm=lm)
            
    except Exception as e:
        logger.error(f"Failed to configure DSPy: {str(e)}")
        raise
    return lm

# Initialize DSPy with default settings when this module is imported
configure_dspy()
