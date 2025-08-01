"""
Integration Agent for handling external service integrations.
"""
from typing import Dict, Any, List, Optional, Union
import json
import logging
from enum import Enum
from pydantic import BaseModel, Field, HttpUrl, validator

from .base_agent import BaseAgent, AgentConfig
from .prompt_loader import PromptLoader

logger = logging.getLogger(__name__)

class IntegrationType(str, Enum):
    """Supported integration types."""
    NOTION = "notion"
    SLACK = "slack"
    GMAIL = "gmail"
    GOOGLE_DRIVE = "google_drive"
    ONEDRIVE = "onedrive"
    DROPBOX = "dropbox"
    GITHUB = "github"
    ZOOM = "zoom"
    CALENDAR = "calendar"

class IntegrationConfig(BaseModel):
    """Configuration for an integration."""
    enabled: bool = True
    api_key: Optional[str] = None
    webhook_url: Optional[HttpUrl] = None
    sync_interval_minutes: int = 60
    default_destination: Optional[str] = None
    custom_headers: Dict[str, str] = {}

class IntegrationAuth(BaseModel):
    """Authentication details for an integration."""
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[int] = None
    token_type: str = "Bearer"

class IntegrationMetadata(BaseModel):
    """Metadata about an integration."""
    name: str
    description: str
    version: str
    icon_url: Optional[HttpUrl] = None
    website: Optional[HttpUrl] = None
    scopes: List[str] = []

class IntegrationAgentConfig(AgentConfig):
    """Configuration for the Integration Agent."""
    max_retry_attempts: int = Field(
        default=3,
        description="Maximum number of retry attempts for failed operations",
        ge=0,
        le=10
    )
    request_timeout: int = Field(
        default=30,
        description="Timeout in seconds for external API requests",
        ge=5,
        le=300
    )
    rate_limit_requests: int = Field(
        default=100,
        description="Maximum number of requests per minute",
        ge=1
    )
    rate_limit_period: int = Field(
        default=60,
        description="Rate limit period in seconds",
        ge=1
    )

class IntegrationResult(BaseModel):
    """Result of an integration operation."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    retryable: bool = False

class IntegrationAgent(BaseAgent[IntegrationAgentConfig]):
    """Agent for managing external service integrations."""
    
    def __init__(self, config: Optional[IntegrationAgentConfig] = None):
        super().__init__(config or self.get_default_config())
        self._integrations: Dict[IntegrationType, IntegrationConfig] = {}
        self._auth_tokens: Dict[IntegrationType, IntegrationAuth] = {}
        self._metadata: Dict[IntegrationType, IntegrationMetadata] = {}
    
    @classmethod
    def get_default_config(cls) -> IntegrationAgentConfig:
        """Return the default configuration for this agent."""
        return IntegrationAgentConfig()
    
    async def process(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process an integration request.
        
        Args:
            input_data: Dictionary containing:
                - action: The action to perform (e.g., 'export', 'import', 'sync')
                - integration: The integration type (e.g., 'notion', 'slack')
                - data: The data to process
                - options: Additional options for the operation
                
        Returns:
            Dictionary containing the operation result
        """
        try:
            # Validate input
            action = input_data.get("action")
            if not action:
                raise ValueError("Input must contain 'action'")
                
            integration_type = input_data.get("integration")
            if not integration_type:
                raise ValueError("Input must contain 'integration'")
                
            # Get the integration config
            integration = self._get_integration(IntegrationType(integration_type))
            
            # Process based on action
            if action == "export":
                result = await self._export_data(integration, input_data)
            elif action == "import":
                result = await self._import_data(integration, input_data)
            elif action == "sync":
                result = await self._sync_data(integration, input_data)
            elif action == "auth":
                result = await self._handle_auth(integration, input_data)
            else:
                raise ValueError(f"Unsupported action: {action}")
            
            return {
                "status": "success",
                "result": result.model_dump()
            }
            
        except Exception as e:
            logger.error(f"Integration error: {str(e)}", exc_info=True)
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def _export_data(self, integration: IntegrationType, data: Dict[str, Any]) -> IntegrationResult:
        """Export data to an external service."""
        try:
            prompt = self._prepare_export_prompt(integration, data)
            llm_response = await self._call_llm(prompt)
            result = self._parse_export_response(llm_response)
            
            # Here you would implement the actual export logic
            # For example, for Notion:
            # if integration == IntegrationType.NOTION:
            #     await self._export_to_notion(data, result)
            
            return IntegrationResult(
                success=True,
                message=f"Successfully exported data to {integration.value}",
                data={"exported": True}
            )
            
        except Exception as e:
            return IntegrationResult(
                success=False,
                message=f"Failed to export data to {integration.value}",
                error=str(e),
                retryable=True
            )
    
    async def _import_data(self, integration: IntegrationType, data: Dict[str, Any]) -> IntegrationResult:
        """Import data from an external service."""
        # Implementation would be similar to _export_data
        pass
    
    async def _sync_data(self, integration: IntegrationType, data: Dict[str, Any]) -> IntegrationResult:
        """Synchronize data with an external service."""
        # Implementation would handle two-way sync
        pass
    
    async def _handle_auth(self, integration: IntegrationType, data: Dict[str, Any]) -> IntegrationResult:
        """Handle OAuth or other authentication flows."""
        # Implementation would handle authentication
        pass
    
    def _prepare_export_prompt(self, integration: IntegrationType, data: Dict[str, Any]) -> str:
        """Prepare the prompt for the LLM."""
        return PromptLoader.render_instruction(
            "integration_export",
            integration=integration.value,
            data=json.dumps(data.get("data", {}), indent=2),
            options=json.dumps(data.get("options", {}), indent=2),
            destination=data.get("destination", "")
        )
    
    def _parse_export_response(self, response: str) -> Dict[str, Any]:
        """Parse the LLM response for export operations."""
        try:
            return json.loads(response)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse export response: {str(e)}")
            return {}
    
    def _get_integration(self, integration_type: IntegrationType) -> IntegrationConfig:
        """Get the configuration for an integration."""
        if integration_type not in self._integrations:
            raise ValueError(f"Integration not configured: {integration_type}")
        return self._integrations[integration_type]
    
    def add_integration(self, 
                       integration_type: IntegrationType, 
                       config: IntegrationConfig,
                       auth: Optional[IntegrationAuth] = None,
                       metadata: Optional[IntegrationMetadata] = None) -> None:
        """Add or update an integration configuration."""
        self._integrations[integration_type] = config
        if auth:
            self._auth_tokens[integration_type] = auth
        if metadata:
            self._metadata[integration_type] = metadata
    
    def remove_integration(self, integration_type: IntegrationType) -> None:
        """Remove an integration configuration."""
        if integration_type in self._integrations:
            del self._integrations[integration_type]
        if integration_type in self._auth_tokens:
            del self._auth_tokens[integration_type]
        if integration_type in self._metadata:
            del self._metadata[integration_type]
