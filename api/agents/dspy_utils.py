"""
DSPy utilities for SynTextAI agents.

This module provides DSPy-based implementations of common NLP tasks,
including key concept extraction with self-improving capabilities.
"""
import logging
import json
import re
import time
from typing import Dict, List, Optional, Any

import dspy
from dspy.teleprompt import BootstrapFewShot
from pydantic import BaseModel, Field, validator

from ..utils.language_utils import validate_language

logger = logging.getLogger(__name__)

# Import our DSPy configuration
from .dspy_config import configure_dspy

# Ensure DSPy is properly configured
configure_dspy()

logger = logging.getLogger(__name__)

class KeyConceptExtractionSignature(dspy.Signature):
    """Signature for key concept extraction using DSPy."""
    document = dspy.InputField(desc="The document text to extract key concepts from")
    language = dspy.InputField(desc="Language of the document")
    comprehension_level = dspy.InputField(
        desc="Target comprehension level (beginner, intermediate, advanced)"
    )
    
    key_concepts = dspy.OutputField(
        desc="List of key concepts, each with title, explanation, and metadata"
    )

class KeyConceptExtractor(dspy.Module):
    """DSPy module for extracting key concepts from documents with improved JSON handling."""
    
    def __init__(self, lm: Optional[dspy.LM] = None):
        super().__init__()
        self.lm = lm or dspy.settings.lm
        self.extractor = dspy.ChainOfThought(KeyConceptExtractionSignature)
        
        # Set the prompt before any compilation
        self.extractor.prompt = self._build_prompt()
        
        # Initialize with diverse examples for few-shot learning
        self.examples = []
        
        # Example 1: Biology
        ex1 = dspy.Example(
            document="""
            Photosynthesis is the process by which green plants and some other organisms 
            use sunlight to synthesize foods with the help of chlorophyll. This process 
            converts carbon dioxide and water into glucose and oxygen.
            """,
            language="english",
            comprehension_level="beginner",
            key_concepts=[
                {
                    "concept_title": "Photosynthesis",
                    "concept_explanation": "The biological process where plants convert light energy into chemical energy in the form of glucose.",
                    "confidence": 0.95
                },
                {
                    "concept_title": "Chlorophyll",
                    "concept_explanation": "The green pigment in plants responsible for absorbing light during photosynthesis.",
                    "confidence": 0.90
                },
                {
                    "concept_title": "Glucose Production",
                    "concept_explanation": "The main product of photosynthesis, a simple sugar used by plants for energy.",
                    "confidence": 0.88
                }
            ]
        ).with_inputs('document', 'language', 'comprehension_level')
        
        # Example 2: Physics
        ex2 = dspy.Example(
            document="""
            Newton's Second Law of Motion states that the acceleration of an object is directly 
            proportional to the net force acting on it and inversely proportional to its mass.
            """,
            language="english",
            comprehension_level="intermediate",
            key_concepts=[
                {
                    "concept_title": "Newton's Second Law",
                    "concept_explanation": "The relationship between an object's mass, the net force acting on it, and its acceleration (F=ma).",
                    "confidence": 0.97
                },
                {
                    "concept_title": "Force",
                    "concept_explanation": "A push or pull acting upon an object, measured in newtons (N).",
                    "confidence": 0.92
                },
                {
                    "concept_title": "Acceleration",
                    "concept_explanation": "The rate of change of velocity of an object with respect to time.",
                    "confidence": 0.90
                },
                {
                    "concept_title": "Net Force",
                    "concept_explanation": "The vector sum of all the forces acting on an object.",
                    "confidence": 0.90
                }
            ]
        ).with_inputs('document', 'language', 'comprehension_level')
        
        self.examples = [ex1, ex2]
        
        # Initialize compiled extractor with few-shot examples
        self.compiled_extractor = self._compile_module()
    
    def _compile_module(self) -> dspy.Module:
        """Compile the module with few-shot examples."""
        try:
            # Create a new list with properly bound examples
            bound_examples = []
            for example in self.examples:
                if not hasattr(example, 'inputs'):
                    # Ensure we have all required fields
                    inputs = {
                        'document': example.document,
                        'language': getattr(example, 'language', 'english'),
                        'comprehension_level': getattr(example, 'comprehension_level', 'intermediate')
                    }
                    bound_example = dspy.Example(**inputs).with_inputs('document', 'language', 'comprehension_level')
                    # Copy over the expected outputs
                    if hasattr(example, 'key_concepts'):
                        bound_example.key_concepts = example.key_concepts
                    bound_examples.append(bound_example)
                else:
                    bound_examples.append(example)
            
            teleprompter = BootstrapFewShot(
                metric=self._validate_key_concepts,
                max_bootstrapped_demos=min(3, len(bound_examples)),
                max_labeled_demos=min(5, len(bound_examples))
            )
            
            compiled = teleprompter.compile(
                student=self.extractor,
                trainset=bound_examples,
                teacher=self.extractor
            )
            
            # Ensure the prompt is properly set on the compiled module
            if not hasattr(compiled, 'prompt') or not compiled.prompt:
                # If the compiled module doesn't have a prompt, set it from the extractor
                if hasattr(self.extractor, 'prompt') and self.extractor.prompt:
                    compiled.prompt = self.extractor.prompt
                else:
                    # Fallback to building a new prompt
                    compiled.prompt = self._build_prompt()
            
            # Ensure the LM is set on the compiled module
            if hasattr(compiled, 'lm') and self.lm:
                compiled.lm = self.lm
                
            return compiled
            
        except Exception as e:
            logger.error(f"Error compiling DSPy module: {str(e)}")
            # Return a basic extractor with default prompt if compilation fails
            self.extractor.prompt = self._build_prompt()
            return self.extractor
    
    def _build_prompt(self) -> str:
        """Build the prompt for key concept extraction with clear JSON formatting."""
        return """Extract key concepts from the following text for a {comprehension_level} level audience.
Language: {language}

Text:
{document}

Return a JSON array of concepts, each with:
- concept_title: Short title (1-5 words)
- concept_explanation: Detailed explanation (2-4 sentences)
- confidence: Score between 0.5 and 1.0

Format your response as a JSON array. Example:
[
  {
    "concept_title": "Example Concept",
    "concept_explanation": "Detailed explanation here...",
    "confidence": 0.95
  }
]

IMPORTANT: Return ONLY the JSON array, no other text or markdown formatting."""
    
    def _validate_key_concepts(
        self, 
        example: dspy.Example, 
        pred: dspy.Example, 
        trace: Optional[Any] = None
    ) -> float:
        """Validate the extracted key concepts with enhanced validation."""
        try:
            if not pred.key_concepts:
                logger.warning("No key concepts in prediction")
                return 0.0
                
            # Ensure we have a list of dictionaries
            if not isinstance(pred.key_concepts, list):
                logger.warning(f"Expected list of concepts, got {type(pred.key_concepts)}")
                return 0.0
                
            # Validate each concept
            valid_concepts = []
            for concept in pred.key_concepts:
                if not isinstance(concept, dict):
                    logger.warning(f"Skipping non-dict concept: {concept}")
                    continue
                    
                # Check required fields
                if not all(k in concept for k in ["concept_title", "concept_explanation"]):
                    logger.warning(f"Concept missing required fields: {concept}")
                    continue
                    
                # Validate confidence score
                confidence = concept.get("confidence", 0.0)
                if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
                    concept["confidence"] = 0.7  # Default confidence if invalid
                    
                valid_concepts.append(concept)
            
            # Update prediction with validated concepts
            pred.key_concepts = valid_concepts
            
            # If no valid concepts, return 0
            if not valid_concepts:
                return 0.0
                
            # If we have expected concepts (during training), calculate F1 score
            if hasattr(example, 'key_concepts') and example.key_concepts:
                expected = set(c["concept_title"].lower() for c in example.key_concepts if c.get("concept_title"))
                predicted = set(c["concept_title"].lower() for c in valid_concepts if c.get("concept_title"))
                
                if not expected or not predicted:
                    return 0.0
                    
                tp = len(expected & predicted)
                fp = len(predicted - expected)
                fn = len(expected - predicted)
                
                precision = tp / (tp + fp) if (tp + fp) > 0 else 0
                recall = tp / (tp + fn) if (tp + fn) > 0 else 0
                f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
                
                return f1
                
            # If no expected concepts, return average confidence of valid concepts
            return sum(c.get("confidence", 0.0) for c in valid_concepts) / len(valid_concepts)
            
        except Exception as e:
            logger.error(f"Error in key concept validation: {str(e)}", exc_info=True)
            return 0.0
    
    def forward(
        self, 
        document: str, 
        language: str = "english", 
        comprehension_level: str = "intermediate"
    ) -> dspy.Prediction:
        """Extract key concepts from the document with improved error handling."""
        try:
            # Ensure document is not empty
            if not document or not document.strip():
                logger.warning("Empty document provided for key concept extraction")
                return dspy.Prediction(key_concepts=[])

            # Validate language
            try:
                language = validate_language(language)
            except ValueError as e:
                logger.warning(f"{str(e)}. Defaulting to English.")
                language = 'english'
                
            # Use the compiled extractor if available, otherwise use the base extractor
            extractor = getattr(self, 'compiled_extractor', self.extractor)
            
            # Log the input for debugging
            logger.debug(f"Extracting concepts from document (first 100 chars): {document[:100]}...")
            
            # Prepare the input as a structured dictionary
            input_data = {
                'document': document,
                'language': language,
                'comprehension_level': comprehension_level
            }
            
            # Get the prediction
            pred = extractor(**input_data)
            
            # Debug log the raw prediction
            logger.debug(f"Raw prediction: {getattr(pred, 'key_concepts', 'No key_concepts in prediction')}")

            # Process the prediction
            if not hasattr(pred, 'key_concepts') or not pred.key_concepts:
                logger.warning("No key concepts found in prediction")
                return dspy.Prediction(key_concepts=[])

            # Handle different response formats
            try:
                import json
                
                # If key_concepts is already a list, validate and return
                if isinstance(pred.key_concepts, list):
                    return self._validate_and_format_concepts(pred.key_concepts)
                    
                # If it's a string, try to parse it as JSON
                if isinstance(pred.key_concepts, str):
                    # Clean the response if it contains markdown code blocks
                    content = pred.key_concepts.strip()
                    if '```json' in content:
                        content = content.split('```json')[1].split('```')[0].strip()
                    elif '```' in content:
                        content = content.split('```')[1].split('```')[0].strip()
                    
                    # Parse the JSON
                    parsed = json.loads(content)
                    
                    # Handle different JSON structures
                    if isinstance(parsed, list):
                        return self._validate_and_format_concepts(parsed)
                    elif isinstance(parsed, dict) and 'key_concepts' in parsed:
                        return self._validate_and_format_concepts(parsed['key_concepts'])
                    else:
                        logger.warning(f"Unexpected JSON structure: {parsed}")
                        return dspy.Prediction(key_concepts=[])
                        
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse key_concepts: {str(e)}\nContent: {getattr(pred, 'key_concepts', '')}")
                return dspy.Prediction(key_concepts=[])
                
            return pred

        except Exception as e:
            logger.error(f"Error in forward pass: {str(e)}", exc_info=True)
            return dspy.Prediction(key_concepts=[])
            
    def _validate_and_format_concepts(self, concepts: List[Any]) -> dspy.Prediction:
        """Validate and format the extracted concepts."""
        valid_concepts = []
        
        for i, concept in enumerate(concepts):
            try:
                # Handle case where concept might be a string
                if not isinstance(concept, dict):
                    concept = {"concept_title": str(concept).strip()}
                
                # Ensure required fields exist
                title = str(concept.get('concept_title', '')).strip()
                explanation = str(concept.get('concept_explanation', '')).strip()
                confidence = float(concept.get('confidence', 0.8))
                
                # Skip empty concepts
                if not title or not explanation:
                    logger.debug(f"Skipping empty concept (title: '{title}', explanation: '{explanation}')")
                    continue
                    
                # Ensure confidence is within valid range
                confidence = max(0.5, min(1.0, confidence))
                
                valid_concepts.append({
                    'concept_title': title,
                    'concept_explanation': explanation,
                    'confidence': confidence
                })
                
            except Exception as e:
                logger.error(f"Error processing concept {i}: {str(e)}")
        
        logger.info(f"Extracted {len(valid_concepts)} valid concepts from {len(concepts)} total")
        return dspy.Prediction(key_concepts=valid_concepts)

def extract_key_concepts(
    document: str, 
    language: str = "english", 
    comprehension_level: str = "intermediate",
    lm: Optional[dspy.LM] = None,
    max_retries: int = 2
) -> List[Dict[str, Any]]:
    """Extract key concepts from a document using DSPy with enhanced reliability.
    
    Args:
        document: The text to extract concepts from
        language: Language of the document (e.g., 'english', 'spanish')
        comprehension_level: Target comprehension level (beginner, intermediate, advanced)
        lm: Optional language model to use (defaults to configured DSPy LM)
        max_retries: Number of retry attempts on failure
        
    Returns:
        List of concept dictionaries with 'concept_title', 'concept_explanation', and 'confidence'
    """
    if not document or not isinstance(document, str) or not document.strip():
        logger.warning("Empty or invalid document provided to extract_key_concepts")
        return []
    
    # Normalize inputs
    language = str(language).lower().strip()
    comprehension_level = str(comprehension_level).lower().strip()
    
    # Validate comprehension level
    if comprehension_level not in ["beginner", "intermediate", "advanced"]:
        logger.warning(f"Invalid comprehension_level '{comprehension_level}'. Defaulting to 'intermediate'")
        comprehension_level = "intermediate"
    
    attempt = 0
    last_error = None
    
    while attempt <= max_retries:
        try:
            # Log the start of extraction
            doc_preview = document[:100] + ("..." if len(document) > 100 else "")
            logger.info(
                f"Extracting key concepts (attempt {attempt + 1}/{max_retries + 1}): "
                f"lang={language}, level={comprehension_level}, length={len(document)} chars"
            )
            logger.debug(f"Document preview: {doc_preview}")
            
            # Initialize extractor with the provided language model
            extractor = KeyConceptExtractor(lm=lm)
            
            # Extract concepts with timing
            start_time = time.time()
            prediction = extractor(
                document=document,
                language=language,
                comprehension_level=comprehension_level
            )
            elapsed = time.time() - start_time
            
            # Get the extracted concepts
            concepts = getattr(prediction, 'key_concepts', [])
            
            # Log the results
            logger.info(
                f"Extracted {len(concepts)} concepts in {elapsed:.2f}s. "
                f"First concept: {concepts[0]['concept_title'] if concepts else 'N/A'}"
            )
            
            # Validate and clean the concepts
            valid_concepts = []
            for i, concept in enumerate(concepts or []):
                if not isinstance(concept, dict):
                    logger.warning(f"Skipping non-dict concept at index {i}: {concept}")
                    continue
                    
                # Ensure required fields
                if not all(k in concept for k in ["concept_title", "concept_explanation"]):
                    logger.warning(f"Concept missing required fields at index {i}: {concept}")
                    continue
                    
                # Clean and validate fields
                concept = {
                    "concept_title": str(concept.get("concept_title", "")).strip(),
                    "concept_explanation": str(concept.get("concept_explanation", "")).strip(),
                    "confidence": min(1.0, max(0.0, float(concept.get("confidence", 0.7))))
                }
                
                # Skip empty concepts
                if not concept["concept_title"] or not concept["concept_explanation"]:
                    logger.warning(f"Skipping empty concept at index {i}")
                    continue
                    
                valid_concepts.append(concept)
            
            logger.info(f"Returning {len(valid_concepts)} valid concepts")
            return valid_concepts
            
        except json.JSONDecodeError as e:
            last_error = f"JSON decode error: {str(e)}"
            logger.error(f"{last_error} (attempt {attempt + 1}/{max_retries + 1})")
            
        except Exception as e:
            last_error = str(e)
            logger.error(
                f"Error in extract_key_concepts (attempt {attempt + 1}/{max_retries + 1}): {str(e)}",
                exc_info=attempt == max_retries  # Only log full traceback on final attempt
            )
            
        # Exponential backoff before retry
        if attempt < max_retries:
            backoff = min(5 * (2 ** attempt), 30)  # Cap at 30 seconds
            logger.info(f"Retrying in {backoff} seconds...")
            time.sleep(backoff)
            
        attempt += 1
    
    # If we get here, all attempts failed
    logger.error(f"Failed to extract key concepts after {max_retries + 1} attempts. Last error: {last_error}")
    return []
