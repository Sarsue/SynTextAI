"""
Utility for loading prompt templates from JSON files.
"""
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional

# Directory containing the prompt templates
PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "prompts", "agents")

class PromptLoader:
    """Loads and caches prompt templates from JSON files."""
    
    _cache: Dict[str, Dict[str, Any]] = {}
    
    @classmethod
    def get_prompt(cls, agent_name: str) -> Dict[str, Any]:
        """
        Get a prompt template for the specified agent.
        
        Args:
            agent_name: Name of the agent (e.g., 'ingestion', 'summarization')
            
        Returns:
            Dictionary containing the prompt template
            
        Raises:
            FileNotFoundError: If the prompt file doesn't exist
            json.JSONDecodeError: If the file contains invalid JSON
        """
        if agent_name not in cls._cache:
            file_path = os.path.join(PROMPTS_DIR, f"{agent_name}.json")
            with open(file_path, 'r', encoding='utf-8') as f:
                cls._cache[agent_name] = json.load(f)
        return cls._cache[agent_name]
    
    @classmethod
    def render_instruction(
        cls, 
        agent_name: str, 
        **kwargs: Any
    ) -> str:
        """
        Render an instruction template with the provided variables.
        
        Args:
            agent_name: Name of the agent
            **kwargs: Variables to inject into the template
            
        Returns:
            Rendered instruction string
        """
        template = cls.get_prompt(agent_name)
        instruction = template.get("instruction_template", "")
        for key, value in kwargs.items():
            placeholder = f"{{{{ {key} }}}}"
            instruction = instruction.replace(placeholder, str(value))
        return instruction.strip()

def get_prompt(agent_name: str) -> Dict[str, Any]:
    """Convenience function to get a prompt template."""
    return PromptLoader.get_prompt(agent_name)

def render_instruction(agent_name: str, **kwargs: Any) -> str:
    """Convenience function to render an instruction."""
    return PromptLoader.render_instruction(agent_name, **kwargs)
