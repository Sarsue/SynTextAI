"""
DSPy utilities for SynTextAI agents.

This module provides DSPy-based implementations of common NLP tasks,
including key concept extraction with self-improving capabilities.
"""
import logging
from typing import List, Dict, Any, Optional
import dspy
from dspy.teleprompt import BootstrapFewShot

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
    """DSPy module for extracting key concepts from documents."""
    
    def __init__(self, lm: Optional[dspy.LM] = None):
        super().__init__()
        self.lm = lm or dspy.settings.lm
        self.extractor = dspy.ChainOfThought(KeyConceptExtractionSignature)
        
        # Initialize with some basic examples for few-shot learning
        self.examples = [
            dspy.Example(
                document="""
                Photosynthesis is the process by which green plants and some other organisms 
                use sunlight to synthesize foods with the help of chlorophyll.
                """,
                language="english",
                comprehension_level="beginner",
                key_concepts=[
                    {
                        "concept_title": "Photosynthesis",
                        "concept_explanation": "The process by which plants use sunlight to create food.",
                        "confidence": 0.95
                    },
                    {
                        "concept_title": "Chlorophyll",
                        "concept_explanation": "Green pigment in plants that absorbs light energy for photosynthesis.",
                        "confidence": 0.90
                    }
                ]
            )
        ]
        
        # Compile the module with few-shot examples
        self.compiled_extractor = self._compile_module()
    
    def _compile_module(self) -> dspy.Module:
        """Compile the module with few-shot examples."""
        teleprompter = BootstrapFewShot(
            metric=self._validate_key_concepts,
            max_bootstrapped_demos=3,
            max_labeled_demos=5
        )
        return teleprompter.compile(
            student=self.extractor,
            trainset=self.examples,
            teacher=self.extractor
        )
    
    def _validate_key_concepts(
        self, 
        example: dspy.Example, 
        pred: dspy.Example, 
        trace: Optional[Any] = None
    ) -> float:
        """Validate the extracted key concepts."""
        if not pred.key_concepts:
            return 0.0
            
        # Simple validation: check if any of the predicted concepts match the expected ones
        expected_concepts = set(
            c["concept_title"].lower() 
            for c in (example.key_concepts or [])
        )
        
        predicted_concepts = set(
            c.get("concept_title", "").lower() 
            for c in pred.key_concepts
            if isinstance(c, dict)
        )
        
        if not expected_concepts:  # If no expected concepts, just check if we got something reasonable
            return min(1.0, len(predicted_concepts) / 5.0)  # Cap at 1.0, normalize by 5
            
        # Calculate F1 score between expected and predicted concepts
        if not predicted_concepts:
            return 0.0
            
        tp = len(expected_concepts & predicted_concepts)
        precision = tp / len(predicted_concepts)
        recall = tp / len(expected_concepts) if expected_concepts else 0.0
        
        if precision + recall == 0:
            return 0.0
            
        f1 = 2 * (precision * recall) / (precision + recall)
        return f1
    
    def forward(
        self, 
        document: str, 
        language: str = "english", 
        comprehension_level: str = "intermediate"
    ) -> dspy.Prediction:
        """Extract key concepts from the document."""
        try:
            # Use the compiled extractor if available, otherwise fall back to the base one
            extractor = getattr(self, 'compiled_extractor', self.extractor)
            
            # Make the prediction
            pred = extractor(
                document=document,
                language=language,
                comprehension_level=comprehension_level
            )
            
            # Post-process the prediction
            if not isinstance(pred.key_concepts, list):
                pred.key_concepts = []
                
            # Ensure each concept has the required fields
            for concept in pred.key_concepts:
                if not isinstance(concept, dict):
                    concept = {"concept_title": str(concept)}
                
                concept.setdefault("concept_title", "")
                concept.setdefault("concept_explanation", "")
                concept.setdefault("confidence", 0.0)
            
            return pred
            
        except Exception as e:
            logger.error(f"Error in key concept extraction: {str(e)}")
            return dspy.Prediction(key_concepts=[])

def extract_key_concepts(
    document: str,
    language: str = "english",
    comprehension_level: str = "intermediate",
    lm: Optional[dspy.LM] = None
) -> List[Dict[str, Any]]:
    """Extract key concepts from a document using DSPy.
    
    Args:
        document: The text content to extract concepts from
        language: Language of the document
        comprehension_level: Target comprehension level (beginner, intermediate, advanced)
        lm: Optional DSPy language model to use
        
    Returns:
        List of key concepts, each with title, explanation, and confidence
    """
    extractor = KeyConceptExtractor(lm=lm)
    result = extractor(
        document=document,
        language=language,
        comprehension_level=comprehension_level
    )
    return result.key_concepts or []
