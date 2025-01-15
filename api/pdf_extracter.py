import re
from io import BytesIO
from pdfminer.high_level import extract_text
from pdfminer.layout import LAParams
from pdfminer.pdfinterp import PDFPageInterpreter
from pdfminer.pdfdevice import PDFDevice
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.converter import TextConverter


def extract_text_with_page_numbers(pdf_data):
    """
    Extracts text from the PDF data (in bytes) while also capturing page numbers.
    
    Args:
        pdf_data (bytes): The PDF file data in byte format.
    
    Returns:
        list: A list of dictionaries containing page numbers and text content.
    """
    laparams = LAParams()
    page_texts = []
    
    # Use BytesIO to treat the PDF data as a file-like object
    with BytesIO(pdf_data) as file:
        resource_manager = PDFResourceManager()
        output = BytesIO()
        device = TextConverter(resource_manager, output, laparams=laparams)
        interpreter = PDFPageInterpreter(resource_manager, device)

        for page_num, page in enumerate(PDFPage.get_pages(file), 1):
            output.seek(0)
            output.truncate()
            interpreter.process_page(page)
            text = output.getvalue().decode("utf-8")
            page_texts.append({"page_num": page_num, "text": text})

    return page_texts

def extract_document_hierarchy(pdf_data):
    """
    Extracts content from the PDF data, including page numbers, and formats it for chunking.
    
    Args:
        pdf_data (bytes): The PDF file data in byte format.
    
    Returns:
        list: A list of chunks containing text and page metadata.
    """
    page_texts = extract_text_with_page_numbers(pdf_data)
    chunks = []

    # Iterate over the extracted pages and build chunk data
    for page in page_texts:
        page_num = page["page_num"]
        text = page["text"]
        
        # Split text into paragraphs (you could adjust based on structure)
        paragraphs = text.split("\n")
        
        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if paragraph:
                # Create a chunk with content and page metadata
                chunk = {
                    "content": paragraph,
                    "page_number": page_num
                }
                chunks.append(chunk)

    return chunks


def main():
    import os
    current_directory = os.getcwd()
    print(current_directory)

    # Replace this with the path to your test PDF
    pdf_file_name = "bitcoin.pdf"
    pdf_path = os.path.join(current_directory, pdf_file_name)
    print(pdf_path)
    query = " how do we solve the problem of double spending?"

    with open(pdf_file_name, "rb") as file:
        pdf_data = file.read()

    print("Extracting document hierarchy...")
    hierarchy = extract_document_hierarchy(pdf_data)
    print(hierarchy)

    # print("Generating embeddings...")
    # hierarchy = generate_embeddings(hierarchy)

    # print("Finding relevant content blocks...")
    # relevant_blocks = find_relevant_blocks(query, hierarchy)
    # print(f"Top relevant blocks: {[block['text'] for block in relevant_blocks]}")

    # print("Performing hierarchical search...")
    # extended_blocks = hierarchical_search(relevant_blocks, hierarchy)
    # print(f"Extended blocks: {[block['text'] for block in extended_blocks]}")

    # print("Deduplicating blocks...")
    # unique_blocks = deduplicate_blocks(extended_blocks)
    # print(f"Unique blocks: {[block['text'] for block in unique_blocks]}")

if __name__ == "__main__":
    main()
