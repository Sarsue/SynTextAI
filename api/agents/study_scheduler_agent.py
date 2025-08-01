"""
Study Scheduler Agent for managing study sessions and spaced repetition.
"""
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import json
import logging
from pydantic import BaseModel, Field, validator

from .base_agent import BaseAgent, AgentConfig
from .prompt_loader import PromptLoader

logger = logging.getLogger(__name__)

class StudySession(BaseModel):
    """Model representing a study session."""
    id: str
    content_id: str
    scheduled_time: datetime
    duration_minutes: int
    priority: int = Field(ge=1, le=5)
    completed: bool = False
    completed_time: Optional[datetime] = None
    notes: Optional[str] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

class StudyPlan(BaseModel):
    """Model representing a study plan with multiple sessions."""
    id: str
    user_id: str
    sessions: List[StudySession] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    @validator('sessions')
    def validate_sessions(cls, v):
        if not v:
            raise ValueError("Study plan must contain at least one session")
        return v

class StudySchedulerConfig(AgentConfig):
    """Configuration for the Study Scheduler Agent."""
    default_session_duration: int = Field(
        default=30,
        description="Default study session duration in minutes",
        ge=5,
        le=240
    )
    max_sessions_per_day: int = Field(
        default=3,
        description="Maximum number of study sessions per day",
        ge=1,
        le=10
    )
    min_interval_days: int = Field(
        default=1,
        description="Minimum days between study sessions for the same content",
        ge=0
    )
    max_interval_days: int = Field(
        default=30,
        description="Maximum days between study sessions for the same content",
        ge=1
    )
    
    @validator('max_interval_days')
    def validate_intervals(cls, v, values):
        if 'min_interval_days' in values and v < values['min_interval_days']:
            raise ValueError("max_interval_days must be greater than or equal to min_interval_days")
        return v

class StudySchedulerAgent(BaseAgent[StudySchedulerConfig]):
    """Agent for managing study sessions and spaced repetition."""
    
    @classmethod
    def get_default_config(cls) -> StudySchedulerConfig:
        """Return the default configuration for this agent."""
        return StudySchedulerConfig()
    
    async def process(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create or update a study plan based on the input data.
        
        Args:
            input_data: Dictionary containing:
                - user_id: ID of the user
                - content_items: List of content items to schedule
                - existing_plan_id: Optional ID of existing plan to update
                - schedule_preferences: User's scheduling preferences
                
        Returns:
            Dictionary containing the study plan and scheduling details
        """
        try:
            # Validate input
            if not input_data.get("user_id"):
                raise ValueError("Input must contain 'user_id'")
            if not input_data.get("content_items"):
                raise ValueError("Input must contain 'content_items'")
            
            # Prepare the prompt
            prompt = self._prepare_prompt(input_data)
            
            # Call LLM to generate schedule
            llm_response = await self._call_llm(prompt)
            
            # Parse and validate the response
            study_plan = self._parse_llm_response(llm_response, input_data["user_id"])
            
            return {
                "status": "success",
                "study_plan": study_plan.model_dump()
            }
            
        except Exception as e:
            logger.error(f"Error generating study schedule: {str(e)}", exc_info=True)
            return {
                "status": "error",
                "error": str(e)
            }
    
    def _prepare_prompt(self, input_data: Dict[str, Any]) -> str:
        """Prepare the prompt for the LLM."""
        return PromptLoader.render_instruction(
            "study_scheduler",
            user_id=input_data["user_id"],
            content_items=json.dumps(input_data["content_items"], indent=2),
            preferences=json.dumps(input_data.get("schedule_preferences", {}), indent=2),
            default_duration=self.config.default_session_duration,
            max_sessions_per_day=self.config.max_sessions_per_day,
            min_interval_days=self.config.min_interval_days,
            max_interval_days=self.config.max_interval_days
        )
    
    async def _call_llm(self, prompt: str) -> str:
        """Call the LLM with the given prompt."""
        # This would be replaced with actual LLM call
        # For now, we'll just return a mock response
        return json.dumps({
            "id": "plan_123",
            "sessions": [
                {
                    "id": "sess_1",
                    "content_id": "content_1",
                    "scheduled_time": (datetime.utcnow() + timedelta(days=1)).isoformat(),
                    "duration_minutes": 30,
                    "priority": 3,
                    "notes": "Review key concepts from Chapter 1"
                },
                {
                    "id": "sess_2",
                    "content_id": "content_2",
                    "scheduled_time": (datetime.utcnow() + timedelta(days=2)).isoformat(),
                    "duration_minutes": 45,
                    "priority": 2,
                    "notes": "Practice problems from Section 3.2"
                }
            ]
        })
    
    def _parse_llm_response(self, response: str, user_id: str) -> StudyPlan:
        """Parse and validate the LLM response."""
        try:
            # Parse JSON response
            data = json.loads(response)
            
            # Basic validation
            if not isinstance(data, dict):
                raise ValueError("Invalid response format: expected JSON object")
                
            if "sessions" not in data or not isinstance(data["sessions"], list):
                raise ValueError("Response must contain a 'sessions' array")
            
            # Create study sessions
            sessions = []
            for i, session_data in enumerate(data["sessions"], 1):
                try:
                    # Convert string timestamps to datetime objects
                    if isinstance(session_data.get("scheduled_time"), str):
                        session_data["scheduled_time"] = datetime.fromisoformat(session_data["scheduled_time"])
                    if isinstance(session_data.get("completed_time"), str):
                        session_data["completed_time"] = datetime.fromisoformat(session_data["completed_time"])
                    
                    sessions.append(StudySession(**session_data))
                except Exception as e:
                    logger.warning(f"Invalid session data at index {i-1}: {str(e)}")
            
            if not sessions:
                raise ValueError("No valid sessions found in response")
            
            # Create and return study plan
            return StudyPlan(
                id=data.get("id", f"plan_{datetime.utcnow().timestamp()}"),
                user_id=user_id,
                sessions=sessions
            )
            
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse LLM response: {str(e)}") from e
