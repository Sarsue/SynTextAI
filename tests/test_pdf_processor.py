"""
Test script for PDF processor.
"""
import asyncio
import os
import sys
from pathlib import Path

# Add the project root to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.repositories import RepositoryManager
from api.processors.pdf_processor import PDFProcessor, process_pdf

async def test_pdf_processor():
    """Test the PDF processor with a sample PDF file."""
    # Initialize repository manager
    repo_manager = RepositoryManager()
    
    # Create a sample PDF file path (replace with an actual PDF file for testing)
    pdf_path = os.path.join(os.path.dirname(__file__), "test_data", "sample.pdf")
    
    if not os.path.exists(pdf_path):
        print(f"Test PDF not found at {pdf_path}")
        print("Please create a test PDF file at tests/test_data/sample.pdf")
        return
    
    # Read the PDF file
    with open(pdf_path, "rb") as f:
        pdf_data = f.read()
    
    # Test the standalone function
    print("Testing process_pdf function...")
    result = await process_pdf(
        file_data=pdf_data,
        file_id=1,
        user_id=1,
        filename="sample.pdf"
    )
    
    print("\nProcess PDF Result:")
    print(f"Success: {result.get('success')}")
    print(f"Error: {result.get('error')}")
    print(f"Pages: {result.get('total_pages', 0)}")
    print(f"Text Length: {len(result.get('text', ''))} characters")
    print(f"Key Concepts: {len(result.get('key_concepts', []))} found")
    
    # Test the processor directly
    print("\nTesting PDFProcessor class...")
    processor = PDFProcessor(repo_manager)
    
    # Test extract_content
    print("\nTesting extract_content...")
    content = await processor.extract_content(
        file_data=pdf_data,
        file_id=1,
        user_id=1,
        filename="sample.pdf"
    )
    print(f"Extraction Success: {content.get('success')}")
    print(f"Pages: {content.get('total_pages', 0)}")
    
    if not content.get('success'):
        print(f"Error: {content.get('error')}")
        return
    
    # Test generate_embeddings
    print("\nTesting generate_embeddings...")
    embeddings = await processor.generate_embeddings(content)
    print(f"Embeddings Success: {embeddings.get('success')}")
    print(f"Chunks: {len(embeddings.get('embeddings', []))}")
    
    if not embeddings.get('success'):
        print(f"Error: {embeddings.get('error')}")
        return
    
    # Test generate_key_concepts
    print("\nTesting generate_key_concepts...")
    key_concepts = await processor.generate_key_concepts(embeddings)
    print(f"Key Concepts: {len(key_concepts)} found")
    
    if key_concepts:
        print("\nSample Key Concept:")
        print(f"Title: {key_concepts[0].get('concept_title')}")
        print(f"Explanation: {key_concepts[0].get('concept_explanation')[:100]}...")

if __name__ == "__main__":
    asyncio.run(test_pdf_processor())
