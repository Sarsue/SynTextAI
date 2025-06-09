import os
import logging
from dotenv import load_dotenv
from mistralai.client import MistralClient
from requests.exceptions import Timeout, RequestException
import requests
import tiktoken
import json
import re
import numpy as np
from sentence_transformers import SentenceTransformer
import time
import google.generativeai as genai
import dspy
from typing import List


# Load environment variables
load_dotenv()
mistral_key = os.getenv("MISTRAL_API_KEY")
google_api_key = os.getenv("GOOGLE_API_KEY")

# Initialize Gemini client and MistralAI client (keep for embeddings if needed)
# Note: genai.Client is deprecated; use genai.configure and GenerativeModel
if google_api_key:
    try:
        genai.configure(api_key=google_api_key)
        # We'll instantiate the model later or use it via dspy.Google
        logging.info("Google GenAI configured.")
    except Exception as e:
        logging.error(f"Failed to configure Google GenAI: {e}")
        # Handle lack of configuration appropriately
else:
    logging.warning("GOOGLE_API_KEY not found in environment variables.")

# Initialize Mistral client if key exists
mistral_client = None
if mistral_key:
    mistral_client = MistralClient(api_key=mistral_key)
    logging.info("Mistral client initialized.")
else:
    logging.warning("MISTRAL_API_KEY not found, Mistral client not initialized (needed for embeddings).")


# --- Updated Model and Token Limit for Gemini --- 
# Use gemini-1.5-pro for large context window (check availability/pricing)
# Fallback: gemini-1.0-pro with ~32k limit
# Choose model appropriate for the task. Flash is fast and cheap, good for bulk explanations.
GEMINI_MODEL_NAME = "gemini-1.5-flash" # Or "gemini-1.5-pro"

# Practical token limits depend on the specific model version and task
# Gemini 1.5 Flash has 1M context, Pro has up to 2M in preview
# Set a reasonable limit for explanations, DSPy manages this but good to be aware
MAX_TOKENS_CONTEXT = 1000000 # Can adjust based on specific Gemini model/needs

delay = 1 # Keep delay if needed for rate limiting (less likely needed with Google)
logging.basicConfig(level=logging.INFO)

# --- DSPy Configuration --- >
gemini_lm = None  # Initialize to None
try:
    # Instantiate dspy.Google with parameters it directly uses for the Google SDK.
    # max_output_tokens is the correct parameter for the Google API.
    gemini_lm = dspy.Google(
        model=GEMINI_MODEL_NAME,
        api_key=google_api_key,
        max_output_tokens=2048  # This is a valid parameter for genai.GenerationConfig
    )
    
    # Configure DSPy settings. dspy.settings.lm.kwargs will reflect gemini_lm.kwargs.
    # It will NOT contain 'max_tokens' at this stage, which is intended.
    dspy.settings.configure(lm=gemini_lm)
    logging.info(f"DSPy configured successfully with Google model: {GEMINI_MODEL_NAME}. LM kwargs from dspy.Google init: {gemini_lm.kwargs}. dspy.settings.lm.kwargs: {dspy.settings.lm.kwargs}")
except Exception as e:
    logging.error(f"Failed to configure DSPy with Google: {e}. Explanations via DSPy may fail.", exc_info=True)
    gemini_lm = None # Explicitly ensure gemini_lm is None if configuration fails

# --- DSPy Signature for Explanation --- >
class GenerateExplanation(dspy.Signature):
    """Generates an explanation for the given context, tailored to the specified language and comprehension level.
    Instructions: Explain the context clearly in the specified language for someone with a {comprehension_level} understanding.
    Focus on the main topic, concept, or significance.
    """
    context = dspy.InputField(desc="The text or transcript segment to explain.")
    language = dspy.InputField(desc="Target language for the explanation (e.g., English, Spanish).", default="English")
    comprehension_level = dspy.InputField(desc="Target comprehension level (e.g., Beginner, Intermediate, Advanced).", default="Beginner")
    explanation = dspy.OutputField(desc="An explanation of the context, tailored to the language and comprehension level.")

# --- DSPy Predictor for Explanation --- >
explain_predictor = dspy.Predict(GenerateExplanation)

# --- DSPy Signature for Key Concept Extraction --- >
class ExtractKeyConcepts(dspy.Signature):
    """You are an expert academic tutor. Your task is to analyze the following {document_type} and extract the key concepts a student should learn from it.
    {content_instruction}
    
    For each key concept, provide:
    1. A concise title for the concept.
    2. A clear and brief explanation of the concept, as if you were explaining it to a student for the first time.
    3. The source location within the document where this concept is most prominently discussed.
        - For PDF documents, provide the page number as an integer (e.g., "source_page_number": 15).
        - For video transcripts, provide the start and end timestamps in seconds (e.g., "source_video_timestamp_start_seconds": 302, "source_video_timestamp_end_seconds": 361).
        - If a precise page or timestamp is not applicable or cannot be determined, use null for that specific source field.

    Please return your response as a JSON array, where each element is an object representing a key concept. Each object should have the following keys:
    - "concept_title": (string) The title of the concept.
    - "concept_explanation": (string) The explanation of the concept.
    - "source_page_number": (integer or null) The page number for PDF sources.
    - "source_video_timestamp_start_seconds": (integer or null) The start timestamp in seconds for video sources.
    - "source_video_timestamp_end_seconds": (integer or null) The end timestamp in seconds for video sources.
    """
    document_content = dspy.InputField(desc="The full text content of the document (PDF text or video transcript).")
    document_type = dspy.InputField(desc="Type of document (e.g., 'PDF document', 'video transcript')", default="document content")
    content_instruction = dspy.InputField(desc="Additional instructions for processing this specific type of content", default="Extract the main concepts, definitions, and key ideas from this content.")
    key_concepts_json = dspy.OutputField(desc="A JSON array of objects, where each object represents a key concept with its title, explanation, and source location (page number or video timestamps).")

# --- DSPy Predictor for Key Concept Extraction --- >
key_concept_extractor = dspy.Predict(ExtractKeyConcepts)

# --- DSPy Signature for Summarization --- >
class SummarizeSignature(dspy.Signature):
    """Generates a concise summary of the provided text, in the specified language and for the target comprehension level.
    Instructions: Create a summary capturing the main points of the document text. Write the summary in {language} for a {comprehension_level} audience.
    """
    document_text = dspy.InputField(desc="The full text of the document to summarize.")
    language = dspy.InputField(desc="Target language for the summary (e.g., English, Spanish).", default="English")
    comprehension_level = dspy.InputField(desc="Target comprehension level (e.g., Beginner, Intermediate, Advanced).", default="Beginner")
    summary = dspy.OutputField(desc="A concise summary of the document, tailored to the language and comprehension level.")

def generate_summary_dspy(text_to_summarize: str, language: str = "English", comprehension_level: str = "Beginner") -> str:
    """Generates a summary using a configured DSPy module."""
    if not google_api_key:
        logging.error("Google API Key not configured. Cannot generate summary.")
        return "Error: API Key not configured."

    try:
        # Configure LM specifically for this function call, similar to explanation
        # This avoids potential issues with global configuration state if other
        # parts of the app use dspy differently.
        temp_lm = dspy.Google(model=GEMINI_MODEL_NAME, api_key=google_api_key)
        with dspy.context(lm=temp_lm):
            # Provide max_tokens via config to dspy.Predict for dsp.generate to use.
            # Adjust max_tokens if a different limit is desired for summaries.
            summary_module = dspy.Predict(SummarizeSignature, config=dict(max_tokens=2048))
            response = summary_module(document_text=text_to_summarize, language=language, comprehension_level=comprehension_level)
            logging.info(f"Successfully generated summary.")
            return response.summary
    except Exception as e:
        logging.error(f"Error generating summary with DSPy: {e}", exc_info=True)
        # Return an error message or None, depending on how caller handles it
        return "Error: Could not generate summary."

def map_concept_to_segments(concept: dict, segments: List[dict], min_segments: int = 1, max_segments: int = 3) -> dict:
    """
    Maps a key concept to the most relevant video transcript segments based on semantic similarity.
    
    Args:
        concept: Dictionary containing key concept information (at minimum 'concept_title' and 'concept_explanation')
        segments: List of transcript segments, each with 'content', 'start_time', and 'end_time'
        min_segments: Minimum number of segments to match (default: 1)
        max_segments: Maximum number of segments to match (default: 3)
    
    Returns:
        The same concept dict with added timestamp information:
        - source_video_timestamp_start_seconds: Start time from earliest matching segment
        - source_video_timestamp_end_seconds: End time from latest matching segment
        - confidence_score: How confident we are in this mapping (0.0-1.0)
    """
    if not concept or not segments or len(segments) == 0:
        logging.warning("Cannot map concept to segments: empty input")
        return concept
    
    # Create a combined representation of the concept
    concept_text = f"{concept.get('concept_title', '')} {concept.get('concept_explanation', '')}".strip()
    if not concept_text:
        logging.warning("Empty concept text, cannot map to segments")
        return concept
    
    try:
        # Get embeddings for the concept
        concept_embedding = get_text_embeddings([concept_text])[0]
        
        # Get embeddings for all segments if not already present
        segment_texts = [segment.get('content', '') for segment in segments]
        segment_embeddings = get_text_embeddings_in_batches(segment_texts) if segment_texts else []
        
        # Calculate similarity scores
        similarities = []
        for i, seg_embedding in enumerate(segment_embeddings):
            if seg_embedding is not None and concept_embedding is not None:
                # Calculate cosine similarity
                similarity = np.dot(concept_embedding, seg_embedding) / (
                    np.linalg.norm(concept_embedding) * np.linalg.norm(seg_embedding))
                similarities.append((i, similarity))
        
        # Sort segments by similarity score (highest first)
        similarities.sort(key=lambda x: x[1], reverse=True)
        
        # Select top segments (between min_segments and max_segments)
        num_segments_to_use = min(max(min_segments, len(similarities) // 10), max_segments)
        best_segments = similarities[:num_segments_to_use] if num_segments_to_use > 0 else []
        
        if best_segments:
            # Get segment indices in chronological order
            sorted_indices = sorted([idx for idx, _ in best_segments])
            
            # Get the start time from the earliest segment and end time from the latest
            start_time = segments[sorted_indices[0]].get('start_time', 0)
            end_time = segments[sorted_indices[-1]].get('end_time', 0)
            
            # Add a small buffer to end time if it's the same as start time
            if end_time <= start_time:
                end_time = start_time + 10  # Add 10 seconds as minimum duration
                
            # Calculate average confidence from selected segments
            avg_confidence = sum([score for _, score in best_segments]) / len(best_segments)
            
            # Add timestamp information to the concept
            concept['source_video_timestamp_start_seconds'] = start_time
            concept['source_video_timestamp_end_seconds'] = end_time
            concept['confidence_score'] = float(avg_confidence)
            
            logging.info(f"Mapped concept '{concept.get('concept_title', '')[0:30]}...' to timestamps: "
                        f"{start_time:.2f}s - {end_time:.2f}s (confidence: {avg_confidence:.3f})")
        else:
            logging.warning("Could not find matching segments for concept")
            # Set default values
            concept['source_video_timestamp_start_seconds'] = 0
            concept['source_video_timestamp_end_seconds'] = 0
            concept['confidence_score'] = 0.0
    
    except Exception as e:
        logging.error(f"Error mapping concept to segments: {e}", exc_info=True)
        # Leave concept unchanged
    
    return concept


def generate_key_concepts_dspy(document_text: str, language: str = "English", comprehension_level: str = "Beginner", is_video: bool = False) -> List[dict]:
    """Generates key concepts with explanations and source links from document text using DSPy.
    
    Args:
        document_text: The full text of the document.
        language: Target language (currently used by prompt, could be dspy.InputField if needed).
        comprehension_level: Target comprehension level (currently used by prompt).
        
    Returns:
        A list of dictionaries, where each dictionary represents a key concept,
        or an empty list if parsing fails or no concepts are found.
    """
    if not gemini_lm: # Check if DSPy LM is configured
        logging.error("DSPy Google LM not configured. Cannot generate key concepts.")
        return []

    # Handle long documents through chunking for more thorough extraction
    max_chunk_size = 8000  # Characters per chunk (adjust based on token limits)
    overlap = 2000  # Overlap between chunks to ensure concepts aren't split
    
    # If document is too long, process it in chunks
    if len(document_text) > max_chunk_size:
        logging.info(f"Document is long ({len(document_text)} chars), processing in chunks with {overlap} char overlap")
        chunks = []
        for i in range(0, len(document_text), max_chunk_size - overlap):
            chunk = document_text[i:i + max_chunk_size]
            chunks.append(chunk)
        
        # Process each chunk and collect concepts
        all_concepts = []
        for i, chunk in enumerate(chunks):
            logging.info(f"Processing chunk {i+1}/{len(chunks)} ({len(chunk)} chars)")
            chunk_concepts = _extract_key_concepts_from_chunk(chunk, language, comprehension_level, is_video, f"Chunk {i+1}/{len(chunks)}")
            all_concepts.extend(chunk_concepts)
        
        # Deduplicate concepts - compare by concept/title similarity
        deduplicated_concepts = _deduplicate_concepts(all_concepts)
        
        # Validate timestamps and page references
        validated_concepts = _validate_references(deduplicated_concepts, document_text, is_video)
        
        # Standardize the output format to use consistent field names
        standardized_concepts = _standardize_concept_format(validated_concepts, is_video)
        return standardized_concepts
    else:
        # For shorter documents, process directly
        concepts = _extract_key_concepts_from_chunk(document_text, language, comprehension_level, is_video)
        validated_concepts = _validate_references(concepts, document_text, is_video)
        
        # Standardize the output format to use consistent field names
        standardized_concepts = _standardize_concept_format(validated_concepts, is_video)
        return standardized_concepts

def _extract_key_concepts_from_chunk(document_chunk: str, language: str, comprehension_level: str, is_video: bool, chunk_info: str = "") -> List[dict]:
    """Extract key concepts from a document chunk with enhanced accuracy."""
    try:
        logging.info(f"Extracting concepts from {chunk_info if chunk_info else 'document'} {'(video)' if is_video else '(text)'} (first 100 chars): {document_chunk[:100]}...")
        
        # Use different specialized prompts for videos vs documents
        if is_video:
            # Enhanced video-specific extraction with timestamp validation
            response = key_concept_extractor(
                document_content=document_chunk,
                document_type="video transcript",
                content_instruction="""Extract the MOST IMPORTANT concepts from this video transcript segment. Be comprehensive and thorough.
                
                TIMESTAMP INSTRUCTIONS (CRITICAL):
                1. Each transcript segment starts with a timestamp in format [MM:SS - MM:SS]
                2. You MUST verify these timestamps exist in the transcript
                3. Only use timestamps that actually appear in the transcript text
                4. For each concept, include the exact start and end timestamps (in seconds) where this concept is discussed
                5. If multiple timestamps discuss the same concept, include all relevant timestamp ranges
                6. DO NOT FABRICATE TIMESTAMPS - only use ones present in the transcript
                
                FORMAT EACH CONCEPT AS:
                {"concept": "Clear title of the concept", 
                 "explanation": "Detailed explanation in simple terms", 
                 "source_timestamp": "MM:SS - MM:SS", 
                 "start_seconds": integer_start_seconds, 
                 "end_seconds": integer_end_seconds}
                
                Be COMPREHENSIVE - extract ALL important concepts, even subtle ones.
                """
            )
        else:
            # Enhanced document processing with page number validation
            response = key_concept_extractor(
                document_content=document_chunk,
                document_type="document",
                content_instruction="""Extract ALL important concepts from this document thoroughly and completely.
                
                PAGE NUMBER INSTRUCTIONS (CRITICAL):
                1. If page numbers exist in the document (format: "Page X"), reference them accurately
                2. Only use page numbers that actually appear in the document text
                3. For each concept, include the exact page number(s) where this concept is discussed
                4. If a concept spans multiple pages, include all relevant page numbers
                5. DO NOT FABRICATE PAGE NUMBERS - only use ones present in the document
                
                FORMAT EACH CONCEPT AS:
                {"concept": "Clear title of the concept", 
                 "explanation": "Detailed explanation in simple terms", 
                 "source_page": "X-Y", 
                 "pages": [X, Y, ...]} 
                
                Be COMPREHENSIVE - extract ALL important concepts, even subtle ones.
                """
            )
        
        # Process the response
        if response and hasattr(response, 'key_concepts_json') and response.key_concepts_json:
            raw_json_output = response.key_concepts_json
            logging.debug(f"DSPy generated key_concepts_json (raw): {raw_json_output[:200]}...")
            
            # Strip markdown fences if present
            match = re.search(r"```(?:json)?\n(.*)\n```", raw_json_output, re.DOTALL)
            if match:
                json_to_parse = match.group(1).strip()
                logging.debug(f"Extracted JSON content: {json_to_parse[:200]}...")
            else:
                json_to_parse = raw_json_output.strip() # Assume it's raw JSON if no fences

            try:
                parsed_concepts = json.loads(json_to_parse)
                if isinstance(parsed_concepts, list):
                    # Ensure each concept has required fields
                    for concept in parsed_concepts:
                        # Check for concept_title/concept_explanation format (from our DSPy model)
                        if "concept_title" in concept and "concept" not in concept:
                            concept["concept"] = concept["concept_title"]
                            
                        if "concept_explanation" in concept and "explanation" not in concept:
                            concept["explanation"] = concept["concept_explanation"]
                            
                        # Apply default values if needed
                        if "concept" not in concept:
                            concept["concept"] = concept.get("title", "Unknown Concept")
                            
                        if "explanation" not in concept:
                            concept["explanation"] = concept.get("description", "")
                            
                    logging.debug(f"Processed concepts: {parsed_concepts[:3]}")
                    return parsed_concepts
                elif isinstance(parsed_concepts, dict) and "concepts" in parsed_concepts:
                    return parsed_concepts["concepts"]
                else:
                    logging.error(f"LLM returned JSON, but it's not a list or expected format: {type(parsed_concepts)}")
                    return [] # Failed to extract properly formatted concepts
            except json.JSONDecodeError as je:
                logging.error(f"Failed to parse JSON from LLM response: {je}")
                logging.error(f"LLM Raw Output: {response.key_concepts_json}")
                return []
        else:
            logging.warning(f"DSPy predictor returned empty or invalid response for key concepts.")
            return []

    except Exception as e:
        logging.error(f"Error extracting key concepts: {e}", exc_info=True)
        return []

def _deduplicate_concepts(concepts: List[dict]) -> List[dict]:
    """Deduplicate concepts based on title/concept similarity."""
    if not concepts:
        return []
    
    # Use a simple approach of comparing lowercase concept titles
    unique_concepts = []
    seen_concepts = set()
    
    for concept in concepts:
        # Check for both field naming conventions (concept_title or concept)
        concept_title = concept.get("concept_title", concept.get("concept", "")).lower().strip()
        # Skip if empty or too similar to existing concepts
        if not concept_title or any(similar_enough(concept_title, seen) for seen in seen_concepts):
            continue
            
        # Log for debugging
        logging.debug(f"Keeping unique concept: '{concept_title[:50]}...'")
        
        seen_concepts.add(concept_title)
        unique_concepts.append(concept)
    
    return unique_concepts

def similar_enough(str1: str, str2: str) -> bool:
    """Check if two strings are similar enough to be considered duplicates.
    Uses a simple approach based on string containment and word overlap."""
    # If either string contains the other entirely, consider them similar
    if str1 in str2 or str2 in str1:
        return True
    
    # Convert to lowercase and split into words
    words1 = set(str1.lower().split())
    words2 = set(str2.lower().split())
    
    # Calculate word overlap ratio
    if not words1 or not words2:
        return False
    
    # Calculate Jaccard similarity (intersection over union)
    intersection = len(words1.intersection(words2))
    union = len(words1.union(words2))
    
    # Consider similar if they share more than 70% of words
    similarity = intersection / union if union > 0 else 0
    return similarity > 0.7  # 70% word overlap threshold


def _standardize_concept_format(concepts: List[dict], is_video: bool = False) -> List[dict]:
    """Standardize concept format to use consistent field names across the application.
    
    This centralizes the format conversion from DSPy output format to application format,
    ensuring all concepts have consistent field names regardless of where they were processed.
    
    Args:
        concepts: List of concept dictionaries, potentially in different formats
        is_video: Whether the concepts are from a video (affects timestamp fields)
        
    Returns:
        List of concepts with standardized field names
    """
    if not concepts:
        return []
        
    standardized_concepts = []
    for concept in concepts:
        # Skip empty concepts
        if not concept:
            continue
            
        # Create a new standardized concept
        standardized = {}
        
        # Handle concept title (DSPy uses 'concept', we use 'concept_title')
        if 'concept_title' in concept:
            standardized['concept_title'] = concept['concept_title']
        elif 'concept' in concept:
            standardized['concept_title'] = concept['concept']
        else:
            standardized['concept_title'] = concept.get('title', 'Untitled Concept')
            
        # Handle explanation (DSPy uses 'explanation', we use 'concept_explanation')
        if 'concept_explanation' in concept:
            standardized['concept_explanation'] = concept['concept_explanation']
        elif 'explanation' in concept:
            standardized['concept_explanation'] = concept['explanation']
        else:
            standardized['concept_explanation'] = concept.get('description', '')
            
        # Debug log to help with troubleshooting
        logging.debug(f"Original concept fields: {list(concept.keys())}")
        logging.debug(f"Standardized to: concept_title='{standardized['concept_title']}', explanation length={len(standardized['concept_explanation'])}")

            
        # Handle video-specific fields
        if is_video:
            # Handle timestamp fields
            if 'start_seconds' in concept:
                standardized['source_video_timestamp_start_seconds'] = concept['start_seconds']
            elif 'source_video_timestamp_start_seconds' in concept:
                standardized['source_video_timestamp_start_seconds'] = concept['source_video_timestamp_start_seconds']
                
            if 'end_seconds' in concept:
                standardized['source_video_timestamp_end_seconds'] = concept['end_seconds']
            elif 'source_video_timestamp_end_seconds' in concept:
                standardized['source_video_timestamp_end_seconds'] = concept['source_video_timestamp_end_seconds']
                
            # Handle source timestamp text
            if 'source_timestamp' in concept:
                standardized['source_video_timestamp'] = concept['source_timestamp']
            elif 'source_video_timestamp' in concept:
                standardized['source_video_timestamp'] = concept['source_video_timestamp']
        # Handle document-specific fields
        else:
            # Handle page number fields
            if 'source_page' in concept:
                standardized['source_page_number'] = concept['source_page']
            elif 'pages' in concept and concept['pages']:
                standardized['source_page_number'] = str(concept['pages'][0])
            elif 'source_page_number' in concept:
                standardized['source_page_number'] = concept['source_page_number']
                
        # Add the standardized concept to our results
        standardized_concepts.append(standardized)
        
    logging.info(f"Standardized {len(standardized_concepts)} concepts to application format")
    return standardized_concepts

def _validate_references(concepts: List[dict], document_text: str, is_video: bool) -> List[dict]:
    """Validate and correct timestamps or page references in the concepts."""
    validated_concepts = []
    
    if is_video:
        # Extract actual timestamps from document
        all_timestamps = re.findall(r'\[(\d+:\d+)\s*-\s*(\d+:\d+)\]', document_text)
        valid_timestamp_ranges = []  # List of (start_seconds, end_seconds) tuples
        
        for start_time, end_time in all_timestamps:
            try:
                start_mins, start_secs = map(int, start_time.split(':'))
                end_mins, end_secs = map(int, end_time.split(':'))
                
                start_seconds = start_mins * 60 + start_secs
                end_seconds = end_mins * 60 + end_secs
                
                valid_timestamp_ranges.append((start_seconds, end_seconds, f"{start_time} - {end_time}"))
            except ValueError:
                continue
        
        # Validate each concept's timestamps
        for concept in concepts:
            start_seconds = concept.get('start_seconds')
            end_seconds = concept.get('end_seconds')
            
            # Check if timestamps are valid and exist in document
            found_valid_timestamp = False
            
            for valid_start, valid_end, timestamp_str in valid_timestamp_ranges:
                # If timestamps overlap with a valid range, use that range
                if ((start_seconds is None) or 
                    (valid_start <= start_seconds <= valid_end) or 
                    (valid_start <= end_seconds <= valid_end) or
                    (start_seconds <= valid_start and end_seconds >= valid_end)):
                    
                    # Update with validated timestamp info
                    concept['start_seconds'] = valid_start
                    concept['end_seconds'] = valid_end
                    concept['source_timestamp'] = timestamp_str
                    found_valid_timestamp = True
                    break
            
            # If no valid timestamp found, use the first timestamp in document
            if not found_valid_timestamp and valid_timestamp_ranges:
                valid_start, valid_end, timestamp_str = valid_timestamp_ranges[0]
                concept['start_seconds'] = valid_start
                concept['end_seconds'] = valid_end
                concept['source_timestamp'] = timestamp_str
                logging.warning(f"Assigned default timestamp to concept: {concept['concept']}")
            
            validated_concepts.append(concept)
    else:
        # Extract actual page numbers from document
        all_pages = re.findall(r'Page\s+(\d+)', document_text)
        valid_pages = [int(page) for page in all_pages]
        
        # Validate each concept's page numbers
        for concept in concepts:
            pages = concept.get('pages', [])
            source_page = concept.get('source_page', '')
            
            # Check if page numbers are valid and exist in document
            valid_concept_pages = [p for p in pages if p in valid_pages]
            
            if not valid_concept_pages and valid_pages:
                # If no valid pages found, use the first page in document
                concept['pages'] = [valid_pages[0]]
                concept['source_page'] = str(valid_pages[0])
                logging.warning(f"Assigned default page to concept: {concept['concept']}")
            elif valid_concept_pages:
                # Update with only valid pages
                concept['pages'] = valid_concept_pages
                concept['source_page'] = '-'.join(map(str, valid_concept_pages))
            
            validated_concepts.append(concept)
    
    return validated_concepts 

# --- Token Counter (Using tiktoken as approximation) ---
# Note: For precise Gemini token counts, use genai.GenerativeModel(MODEL_NAME).count_tokens(text)
# but tiktoken provides a reasonable estimate for checks.
def token_count(text):
    """Approximate token count using tiktoken."""
    # Choose an appropriate encoding, cl100k_base is common
    encoding = tiktoken.get_encoding("cl100k_base") 
    return len(encoding.encode(text))

def extract_image_text(base64_image):
    """Extract text from an image using the Mistral API."""
    url = "https://api.mistral.ai/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {mistral_key}",
    }
    data = {
        "model": "pixtral-12b-2409",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What’s in this image?"},
                    {"type": "image_url",
                        "image_url": f"data:image/jpeg;base64,{base64_image}"}
                ]
            }
        ],
        "max_tokens": 1500
    }
    TIMEOUT_SECONDS = 10 
    response = requests.post(url, headers=headers,
                             json=data, timeout=TIMEOUT_SECONDS)
    if response.status_code == 200:
        return response.json()['choices'][0]['message']['content']
    else:
        logging.error(
            f"Error extracting text: {response.status_code} - {response.text}")
        return ""

def prompt_llm(query):
    """Prompt the configured Google Generative AI model. [DEPRECATED - Use DSPy modules]"""
    logging.warning("Direct call to prompt_llm is deprecated for structured tasks like explanations. Use DSPy modules instead.")

    if not google_api_key:
        logging.error("Google API Key not configured.")
        return "Error: Google API Key not set."
    
    try:
 
        response = client.models.generate_content(
        model= GEMINI_MODEL_NAME,
        contents=query
        )
       # print(response.text)

        print(response.model_dump_json(
            exclude_none=True, indent=4))
        return response.text

    except Exception as e:
        logging.error(f"Error interacting with Google GenAI: {e}", exc_info=True)
        # Consider more specific error handling based on google.api_core.exceptions
        return f"Error: Could not get response from LLM. {e}"

# --- New DSPy-based Explanation Function --- >
def generate_explanation_dspy(text_chunk: str, language: str = "English", comprehension_level: str = "Beginner", max_context_length: int = 2000) -> str:
    """Generates an explanation for a text chunk using DSPy and Gemini."""
    if not text_chunk:
        logging.warning("generate_explanation_dspy called with empty text_chunk.")
        return ""

    # Truncate context if necessary (DSPy might handle this, but belt-and-suspenders)
    truncated_chunk = text_chunk[:max_context_length]

    try:
        # Ensure DSPy was configured
        if not dspy.settings.lm:
            logging.error("DSPy LM is not configured. Cannot generate explanation.")
            return "Error: LLM service not configured."
            
        response = explain_predictor(context=truncated_chunk, language=language, comprehension_level=comprehension_level)
        if response and hasattr(response, 'explanation') and response.explanation:
             logging.debug(f"DSPy generated explanation: {response.explanation[:50]}...") # Log snippet
             return response.explanation
        else:
            logging.warning(f"DSPy predictor returned empty or invalid response for chunk: {truncated_chunk[:50]}...")
            return ""

    except Exception as e:
        logging.error(f"Error generating explanation with DSPy: {e}", exc_info=True)
        # Return empty string or a specific error message depending on desired behavior
        return "Error: Failed to generate explanation."

def get_text_embedding(input):
    # Ensure client is initialized before use
    if not mistral_client:
        logging.error("Mistral client not initialized. Cannot get embedding.")
        # Option: raise error or return None/empty list based on expected handling
        return [] # Returning empty list might require downstream checks
        
    try:
        embeddings_batch_response = mistral_client.embeddings(
            model="mistral-embed",
            input=input
        )
        # Add check for data existence
        if embeddings_batch_response.data:
            return embeddings_batch_response.data[0].embedding
        else:
            logging.warning(f"Mistral embedding returned no data for input: {str(input)[:50]}...")
            return []
    except Exception as e:
        logging.error(f"Error getting embedding from Mistral: {e}", exc_info=True)
        return [] # Return empty list on error

# --- Embeddings function (Still uses Mistral, update if needed) ---
def get_text_embeddings_in_batches(inputs, batch_size=10):
    """
    Generate embeddings for a list of inputs in batches using Mistral.
    Update this if you want to use Google's embedding models.
    """
    if not mistral_key:
        logging.error("Mistral API Key not configured for embeddings.")
        return []
    # Ensure client is initialized before use
    if not mistral_client:
        logging.error("Mistral client not initialized. Cannot get embeddings in batch.")
        return []
        
    all_embeddings = []
    max_retries = 3
    retry_delay = 2 # seconds
    
    for i in range(0, len(inputs), batch_size):
        batch = inputs[i:i + batch_size]
        retries = 0
        success = False
        while retries < max_retries and not success:
            try:
                embeddings_batch = mistral_client.embeddings(model="mistral-embed", input=batch)
                all_embeddings.extend([emb.embedding for emb in embeddings_batch.data])
                success = True
            except Exception as e:
                retries += 1
                logging.warning(f"Error getting embeddings for batch starting at index {i} (Attempt {retries}/{max_retries}): {e}")
                if retries < max_retries:
                    logging.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    logging.error(f"Failed to get embeddings for batch starting at index {i} after {max_retries} attempts.")
                    # Option 1: Re-raise the last exception to signal critical failure
                    raise e 
                    # Option 2: Return partially collected embeddings (might be risky)
                    # return all_embeddings 
                    # Option 3: Append placeholders (like None) or skip the batch
                    # all_embeddings.extend([None] * len(batch)) # Mark failures
                    # break # Stop processing further batches if one fails critically
                    
        # If a batch failed critically and we didn't re-raise, handle here
        # if not success:
            # Handle critical failure after retries if needed (e.g., if Option 3 above was chosen)
            # pass
            
        # Apply the original delay *between batches* if needed for general rate limiting
        time.sleep(delay) # Use the original 'delay' variable if defined elsewhere

    return all_embeddings

if __name__ == "__main__":
   pass