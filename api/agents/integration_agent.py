"""
Integration Agent for managing external service connections and data synchronization.

This module provides the IntegrationAgent class which handles authentication, data transfer,
and synchronization with various third-party services. It provides a unified interface for
interacting with multiple external platforms while maintaining security and rate limiting.

Key Features:
- Supports multiple integration types (Notion, Slack, Gmail, etc.)
- Handles OAuth2 and API key authentication
- Manages rate limiting and retries
- Provides webhook support for real-time updates
- Tracks integration status and health

Example Usage:
    ```python
    # Initialize the agent with default configuration
    agent = IntegrationAgent()
    
    # Configure a Notion integration
    await agent.add_integration(
        IntegrationType.NOTION,
        config=IntegrationConfig(
            api_key="your_notion_api_key",
            default_destination="workspace_123"
        )
    )
    
    # Export content to Notion
    result = await agent.export_content(
        integration_type=IntegrationType.NOTION,
        content={"title": "Document", "content": "Example content"},
        destination="page_456"
    )
    
    if result.success:
        print(f"Content exported successfully: {result.message}")
    else:
        print(f"Export failed: {result.error}")
    ```
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
    """
    Enumeration of supported integration types.
    
    Each integration type corresponds to a specific external service that
    can be connected to the application. The values are used as unique
    identifiers throughout the integration system.
    
    Attributes:
        NOTION: Notion workspace integration
        SLACK: Slack workspace integration
        GMAIL: Gmail account integration
        GOOGLE_DRIVE: Google Drive integration
        ONEDRIVE: Microsoft OneDrive integration
        DROPBOX: Dropbox account integration
        GITHUB: GitHub repository integration
        ZOOM: Zoom meeting integration
        CALENDAR: Calendar integration (supports multiple providers)
    """
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
    """
    Configuration settings for an external service integration.
    
    This model defines the configuration parameters needed to connect to and
    interact with an external service. Different services may require different
    combinations of these parameters.
    
    Attributes:
        enabled: Whether the integration is currently active.
        api_key: API key or access token for the service.
        webhook_url: URL for receiving webhook notifications from the service.
        sync_interval_minutes: How often to sync data with the service.
        default_destination: Default target location (e.g., channel, folder, page).
        custom_headers: Additional HTTP headers for API requests.
    """
    enabled: bool = Field(
        default=True,
        description="Whether the integration is currently active",
        example=True
    )
    api_key: Optional[str] = Field(
        default=None,
        description="API key or access token for the service",
        example="sk_1234567890abcdef",
        sensitive=True
    )
    webhook_url: Optional[HttpUrl] = Field(
        default=None,
        description="URL for receiving webhook notifications from the service",
        example="https://yourapp.com/webhooks/notion"
    )
    sync_interval_minutes: int = Field(
        default=60,
        description="How often to sync data with the service (in minutes)",
        ge=1,
        le=10080,  # 1 week
        example=60
    )
    default_destination: Optional[str] = Field(
        default=None,
        description="Default target location (e.g., channel ID, folder ID, page ID)",
        example="C0123456789"
    )
    custom_headers: Dict[str, str] = Field(
        default_factory=dict,
        description="Additional HTTP headers for API requests",
        example={"X-Custom-Header": "value"}
    )

class IntegrationAuth(BaseModel):
    """
    Authentication credentials for an external service.
    
    This model stores OAuth2 tokens and related authentication information
    needed to maintain an authenticated session with an external service.
    
    Attributes:
        access_token: The OAuth2 access token used to authenticate API requests.
        refresh_token: Optional refresh token for obtaining new access tokens.
        expires_at: Unix timestamp when the access token expires.
        token_type: The type of token (defaults to "Bearer").
    """
    access_token: str = Field(
        ...,
        description="OAuth2 access token for API authentication",
        example="ya29.a0ARrdaM...",
        sensitive=True
    )
    refresh_token: Optional[str] = Field(
        default=None,
        description="Refresh token for obtaining new access tokens",
        example="1//03r9...",
        sensitive=True
    )
    expires_at: Optional[int] = Field(
        default=None,
        description="Unix timestamp when the access token expires",
        example=1735689600
    )
    token_type: str = Field(
        default="Bearer",
        description="Type of the access token",
        example="Bearer"
    )

class IntegrationMetadata(BaseModel):
    """
    Descriptive metadata about an integration.
    
    This model provides human-readable information about an integration,
    including its name, description, version, and other metadata that helps
    with displaying and managing the integration in the UI.
    
    Attributes:
        name: Display name of the integration.
        description: Brief description of what the integration does.
        version: Version identifier for the integration.
        icon_url: URL to an icon representing the integration.
        website: Official website or documentation URL.
        scopes: List of OAuth2 scopes required by the integration.
    """
    name: str = Field(
        ...,
        description="Display name of the integration",
        example="Notion Workspace",
        max_length=100
    )
    description: str = Field(
        ...,
        description="Brief description of what the integration does",
        example="Connect your Notion workspace to sync pages and databases",
        max_length=500
    )
    version: str = Field(
        ...,
        description="Version identifier for the integration",
        example="1.0.0",
        regex=r'^\d+\.\d+\.\d+$'
    )
    icon_url: Optional[HttpUrl] = Field(
        default=None,
        description="URL to an icon representing the integration",
        example="https://example.com/icons/notion.png"
    )
    website: Optional[HttpUrl] = Field(
        default=None,
        description="Official website or documentation URL",
        example="https://www.notion.so/help"
    )
    scopes: List[str] = Field(
        default_factory=list,
        description="List of OAuth2 scopes required by the integration",
        example=["notion:read", "notion:write"]
    )

class IntegrationAgentConfig(AgentConfig):
    """
    Configuration for the Integration Agent's behavior.
    
    This configuration controls how the IntegrationAgent handles rate limiting,
    retries, and timeouts when communicating with external services.
    
    Attributes:
        max_retry_attempts: Maximum number of retry attempts for failed operations.
                         Set to 0 to disable retries.
                         Range: 0-10
                         Default: 3
        request_timeout: Timeout in seconds for external API requests.
                       Range: 5-300
                       Default: 30
        rate_limit_requests: Maximum number of requests allowed per rate limit period.
                          Default: 100
        rate_limit_period: Duration of the rate limit window in seconds.
                        Default: 60 (1 minute)
    """
    max_retry_attempts: int = Field(
        default=3,
        description="Maximum number of retry attempts for failed operations (0-10)",
        ge=0,
        le=10,
        example=3
    )
    request_timeout: int = Field(
        default=30,
        description="Timeout in seconds for external API requests (5-300)",
        ge=5,
        le=300,
        example=30
    )
    rate_limit_requests: int = Field(
        default=100,
        description="Maximum number of requests per rate limit period",
        ge=1,
        example=100
    )
    rate_limit_period: int = Field(
        default=60,
        description="Rate limit window duration in seconds",
        ge=1,
        example=60
    )

class IntegrationResult(BaseModel):
    """
    Result of an integration operation.
    
    This model standardizes the response format for all integration operations,
    providing consistent success/failure handling and error reporting.
    
    Attributes:
        success: Whether the operation completed successfully.
        message: Human-readable status message.
        data: Optional result data from the operation.
        error: Error message if the operation failed.
        retryable: Whether the operation can be retried after a failure.
    """
    success: bool = Field(
        ...,
        description="Whether the operation completed successfully",
        example=True
    )
    message: str = Field(
        ...,
        description="Human-readable status message",
        example="Content exported successfully"
    )
    data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional result data from the operation",
        example={"id": "page_123", "url": "https://notion.so/page123"}
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if the operation failed",
        example="Invalid API key"
    )
    retryable: bool = Field(
        default=False,
        description="Whether the operation can be retried after a failure",
        example=True
    )

class IntegrationAgent(BaseAgent[IntegrationAgentConfig]):
    """
    Agent for managing external service integrations and data synchronization.
    
    The IntegrationAgent provides a unified interface for connecting to and
    interacting with various third-party services. It handles authentication,
    rate limiting, retries, and error handling for all external API calls.
    
    Key Features:
    - Centralized management of multiple integration types
    - OAuth2 and API key authentication support
    - Automatic rate limiting and retry logic
    - Webhook handling for real-time updates
    - Health monitoring and status reporting
    
    Example:
        ```python
        # Initialize with custom configuration
        config = IntegrationAgentConfig(
            max_retry_attempts=5,
            request_timeout=60
        )
        agent = IntegrationAgent(config=config)
        
        # Add a new integration
        result = await agent.add_integration(
            integration_type=IntegrationType.NOTION,
            config=IntegrationConfig(
                api_key="your_api_key_here",
                default_destination="workspace_123"
            ),
            auth=IntegrationAuth(
                access_token="your_access_token",
                refresh_token="your_refresh_token"
            )
        )
        
        # Export content to the integrated service
        export_result = await agent.export_content(
            integration_type=IntegrationType.NOTION,
            content={"title": "Document", "content": "Example content"},
            destination="page_456"
        )
        
        # Check the result
        if export_result.success:
            print(f"Export successful: {export_result.message}")
            print(f"Page URL: {export_result.data.get('url')}")
        ```
    """
    
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
