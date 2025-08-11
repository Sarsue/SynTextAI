#!/usr/bin/env python3
"""
Test script for verifying the AgentService and agent functionality.

This script tests the agent service by creating an instance and processing
a sample document through the ingestion pipeline.
"""

import asyncio
import sys
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Import the agent service
from api.services.agent_service import AgentService

async def test_agent_service():
    """Test the agent service with a sample document."""
    print("Starting agent service test...")
    
    try:
        # Initialize the agent service
        agent_service = AgentService()
        print("Agent service initialized successfully")
        
        # List available agents
        agents = agent_service.list_agents()
        print(f"Available agents: {list(agents.keys())}")
        
        # Test the ingestion agent with a sample document
        test_doc = {
            "content": """
            Artificial intelligence (AI) is intelligence demonstrated by machines, as opposed to 
            the natural intelligence displayed by animals including humans. AI research has been 
            defined as the field of study of intelligent agents, which refers to any system that 
            perceives its environment and takes actions that maximize its chance of achieving its goals.
            """,
            "metadata": {
                "title": "AI Introduction",
                "source": "test",
                "content_type": "text/plain"
            }
        }
        
        print("\nProcessing test document with ingestion agent...")
        result = await agent_service.process("ingestion", test_doc)
        
        print("\nProcessing result:")
        print(f"Status: {result.get('status')}")
        print(f"Key concepts: {len(result.get('key_concepts', []))} found")
        print(f"Flashcards: {len(result.get('flashcards', []))} generated")
        print(f"Quizzes: {len(result.get('quizzes', []))} generated")
        
        return True
    
    except Exception as e:
        print(f"Error during agent service test: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    # Run the test
    success = asyncio.run(test_agent_service())
    
    if success:
        print("\n✅ Agent service test completed successfully!")
    else:
        print("\n❌ Agent service test failed!")
        sys.exit(1)
