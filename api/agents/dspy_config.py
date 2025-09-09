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
    
    def basic_request(self, prompt, **kwargs):
        """Handle both string and structured prompts, with improved JSON response handling."""
        # Store the original prompt for debugging
        original_prompt = prompt
        
        # Handle dictionary/structured prompts
        if isinstance(prompt, dict):
            # Format the prompt using the template
            prompt_template = """Extract key concepts from the following text for a {comprehension_level} level audience.
Language: {language}

Text:
{document}

Return a JSON array of concepts, each with:
- concept_title: Short title (1-5 words)
- concept_explanation: Detailed explanation (2-4 sentences)
- confidence: Score between 0.5 and 1.0

Format your response as a JSON array. Example:
[
  {{
    "concept_title": "Example Concept",
    "concept_explanation": "Detailed explanation here...",
    "confidence": 0.95
  }}
]
"""
            prompt = prompt_template.format(
                document=prompt.get('document', ''),
                language=prompt.get('language', 'english'),
                comprehension_level=prompt.get('comprehension_level', 'intermediate')
            )
        
        # Store the prompt in history for debugging
        self.history.append({"prompt": prompt, "original_prompt": original_prompt, "kwargs": kwargs})
        
        # Set better defaults for key concept extraction
        temperature = kwargs.get("temperature", 0.3)
        max_tokens = kwargs.get("max_tokens", 2000)
        
        try:
            # Get the response from the LLM service
            response = llm_service.generate_text(
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                provider=kwargs.get("provider", "openai"),
                model=kwargs.get("model", "gpt-4")
            )
            
            # Ensure response is a string
            if not isinstance(response, str):
                response = str(response)
                
            # Clean the response (remove markdown code blocks if present)
            clean_response = response.strip()
            if '```json' in clean_response:
                clean_response = clean_response.split('```json')[1].split('```')[0].strip()
            elif '```' in clean_response:
                clean_response = clean_response.split('```')[1].split('```')[0].strip()
                
            # Log the cleaned response for debugging
            logger.debug(f"LLM Response: {clean_response[:200]}..." if len(clean_response) > 200 else f"LLM Response: {clean_response}")
            
            # Return the response in the format DSPy expects
            return {
                "choices": [{
                    "message": {
                        "content": clean_response
                    }
                }]
            }
        except Exception as e:
            logger.error(f"Error in DSPy LLM request: {str(e)}")
            raise
    
    def _validate_prompt(self, prompt: str) -> None:
        """Validate the prompt before sending to LLM."""
        if not prompt or not isinstance(prompt, str):
            raise ValueError("Prompt must be a non-empty string")
            
        # Check for minimum length (excluding whitespace)
        if len(prompt.strip()) < 10:  # Arbitrary minimum length
            raise ValueError("Prompt is too short. Please provide more context.")
            
        # Check for maximum length (prevent potential token limit issues)
        if len(prompt) > 10000:  # Arbitrary max length
            raise ValueError("Prompt is too long. Please reduce the length.")
    
    def __call__(self, prompt=None, **kwargs):
        """Handle the LLM call with improved prompt handling and error recovery."""
        # Handle different ways prompt can be passed
        if prompt is None and 'prompt' in kwargs:
            prompt = kwargs.pop('prompt')
        elif prompt is None and 'prompt' not in kwargs and len(kwargs) > 0:
            # Try to get the first argument if prompt is not explicitly named
            first_arg = next(iter(kwargs.values()))
            if isinstance(first_arg, (str, dict)):
                prompt = first_arg
                # Remove the used argument
                kwargs.pop(next(iter(kwargs)))
        
        if prompt is None:
            logger.error("No prompt provided to DSPyLLM")
            return [""]  # Return empty response instead of raising
        
        try:
            # Log the prompt (truncated) and kwargs for debugging
            if isinstance(prompt, dict):
                logger.debug(f"DSPyLLM called with structured prompt (keys: {list(prompt.keys())})")
            else:
                logger.debug(f"DSPyLLM called with prompt: {str(prompt)[:200]}...")
                
            if len(kwargs) > 0:
                logger.debug(f"DSPyLLM kwargs: {kwargs}")
            
            # Make the request
            response = self.basic_request(prompt, **kwargs)
            
            # Validate response structure
            if not response or not isinstance(response, dict):
                logger.error(f"Invalid response format from LLM: {response}")
                return [""]
                
            # Extract the content from the response
            try:
                content = response['choices'][0]['message']['content']
                return [content]
            except (KeyError, IndexError) as e:
                logger.error(f"Failed to extract content from response: {e}")
                return [""]
                
        except Exception as e:
            logger.error(f"Error in DSPyLLM call: {str(e)}", exc_info=True)
            return [""]  # Return empty response instead of raising

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
