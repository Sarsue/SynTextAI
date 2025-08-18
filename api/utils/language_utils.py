"""
Language utility functions for handling language codes and names.
"""
from typing import Dict, Optional, List
import logging

logger = logging.getLogger(__name__)

class LanguageUtils:
    """Utility class for language code and name conversions."""
    
    # Map of language codes to full names
    LANGUAGE_MAP = {
        'en': 'english',
        'es': 'spanish',
        'fr': 'french',
        'de': 'german',
        'it': 'italian',
        'pt': 'portuguese',
        'ru': 'russian',
        'zh': 'chinese',
        'ja': 'japanese',
        'ko': 'korean',
        # Add full names as identity mappings
        'english': 'english',
        'spanish': 'spanish',
        'french': 'french',
        'german': 'german',
        'italian': 'italian',
        'portuguese': 'portuguese',
        'russian': 'russian',
        'chinese': 'chinese',
        'japanese': 'japanese',
        'korean': 'korean',
    }
    
    # Supported languages (full names)
    SUPPORTED_LANGUAGES = [
        'english', 'spanish', 'french', 'german', 'italian',
        'portuguese', 'russian', 'chinese', 'japanese', 'korean'
    ]
    
    @classmethod
    def get_language_name(cls, language_code: str) -> str:
        """
        Convert a language code to its full name.
        
        Args:
            language_code: Language code (e.g., 'en', 'es') or full name (e.g., 'english')
            
        Returns:
            str: Full language name in lowercase, or 'english' if not found
        """
        if not language_code:
            return 'english'
            
        language_code = str(language_code).lower().strip()
        return cls.LANGUAGE_MAP.get(language_code, 'english')
    
    @classmethod
    def is_language_supported(cls, language: str) -> bool:
        """
        Check if a language is supported.
        
        Args:
            language: Language code or name to check
            
        Returns:
            bool: True if the language is supported, False otherwise
        """
        if not language:
            return False
            
        language = str(language).lower().strip()
        
        # Check if it's a code
        if language in cls.LANGUAGE_MAP:
            return True
            
        # Check if it's a full name
        return language in cls.SUPPORTED_LANGUAGES
    
    @classmethod
    def get_supported_languages(cls) -> List[Dict[str, str]]:
        """
        Get a list of supported languages with their codes and names.
        
        Returns:
            List[Dict[str, str]]: List of dicts with 'code' and 'name' keys
        """
        return [
            {'code': code, 'name': name}
            for code, name in cls.LANGUAGE_MAP.items()
            if len(code) == 2  # Only include two-letter codes
        ]
    
    @classmethod
    def validate_language(cls, language: str) -> str:
        """
        Validate a language code or name and return the full name.
        
        Args:
            language: Language code or name to validate
            
        Returns:
            str: Full language name if valid, 'english' otherwise
            
        Raises:
            ValueError: If the language is not supported
        """
        if not language:
            return 'english'
            
        language = str(language).lower().strip()
        
        # Get full name if it's a code
        if language in cls.LANGUAGE_MAP:
            return cls.LANGUAGE_MAP[language]
            
        # Check if it's already a full name
        if language in cls.SUPPORTED_LANGUAGES:
            return language
            
        # If we get here, the language is not supported
        supported = ", ".join(sorted({v for k, v in cls.LANGUAGE_MAP.items() if len(k) == 2}))
        raise ValueError(
            f"Language '{language}' is not supported. "
            f"Supported languages: {supported}"
        )

# Helper functions for easier access
get_language_name = LanguageUtils.get_language_name
is_language_supported = LanguageUtils.is_language_supported
get_supported_languages = LanguageUtils.get_supported_languages
validate_language = LanguageUtils.validate_language
