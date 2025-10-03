* Document ingestion
    * Upload PDF, paste text, or input YouTube link.
    * Basic text extraction and parsing (works consistently).
* Summarization engine
    * Auto-generate concise summaries for uploaded documents.
* Flashcard generation
    * Create Q/A cards from key concepts in the document.
    * Export as CSV or simple shareable format.
* Quiz generation
    * Multiple choice or short-answer quizzes generated from text.


To Do
* Search within documents
    * Simple keyword-based retrieval + context display.
* Simple UI
    * Web app with drag-and-drop or paste input.
    * Gamify dashboard: Summaries, Flashcards, Quizzes tabs.
syntext on Azure 

With these, you can already run demos and charge for team pilots.


uvicorn api.app:app --reload --host 0.0.0.0 --port 3000

docker-compose -f docker-compose.yml -f docker-compose.local.yml up --build && docker-compose logs -f